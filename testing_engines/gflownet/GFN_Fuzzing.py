import os
import shutil
import sys
import time
import websockets
import json
import asyncio
from datetime import datetime
import logging

from testing_engines.gflownet.generator.proxy.proxy_config import proxy_args
from testing_engines.gflownet.generator.proxy.train_proxy import train_proxy
from generator.generative_model.main import generate_samples_with_gfn
from generator.pre_process.transform_actions import decode, encode
from testing_engines.gflownet.lib.monitor import Monitor
from testing_engines.gflownet.path_config import path_args


async def test_one_scenario(scenario_testcase, specs, covered_specs, reward, directory=None) -> object:
    uri = "ws://localhost:8000"  # The Ip and port for our customized bridge.
    async with websockets.connect(uri, max_size=300000000, ping_interval=None) as websocket:
        # Initialize files
        init_msg = json.dumps({'CMD': "CMD_READY_FOR_NEW_TEST"})
        await websocket.send(init_msg)
        while True:
            msg = await websocket.recv()
            msg = json.loads(msg)
            # print(msg['TYPE'])
            if msg['TYPE'] == 'READY_FOR_NEW_TEST':
                if msg['DATA']:
                    logging.info('Running Scenario: {}'.format(scenario_testcase["ScenarioName"]))
                    send_command_msg = {'CMD': "CMD_NEW_TEST", 'DATA': scenario_testcase}
                    await websocket.send(json.dumps(send_command_msg))
                else:
                    time.sleep(3)
                    init_msg = json.dumps({'CMD': "CMD_READY_FOR_NEW_TEST"})
                    await websocket.send(init_msg)
            elif msg['TYPE'] == 'KEEP_SERVER_AND_CLIENT_ALIVE':
                send_msg = {'CMD': "KEEP_SERVER_AND_CLIENT_ALIVE", 'DATA': None}
                await websocket.send(json.dumps(send_msg))
            elif msg['TYPE'] == 'TEST_TERMINATED' or msg['TYPE'] == 'ERROR':
                print("Try to reconnect.")
                time.sleep(3)
                init_msg = json.dumps({'CMD': "CMD_READY_FOR_NEW_TEST"})
                await websocket.send(init_msg)
            elif msg['TYPE'] == 'TEST_COMPLETED':
                output_trace = msg['DATA']
                dt_string = datetime.now().strftime("%d-%m-%Y-%H-%M-%S")
                file = directory + '/data/result' + dt_string + '.json'
                with open(file, 'w') as outfile:
                    json.dump(output_trace, outfile, indent=2)
                logging.info("The number of states in the trace is {}".format(len(output_trace['trace'])))
                if not output_trace['destinationReached']:
                    with open(directory + '/Incompleted.txt', 'a') as f:
                        json.dump(scenario_testcase, f, indent=2)
                        f.write('\n')
                if len(output_trace['trace']) > 1:
                    if 'Accident!' in output_trace["testFailures"]:
                        with open(directory + '/AccidentTestCase.txt', 'a') as bug_file:
                            now = datetime.now()
                            dt_string = now.strftime("%d-%m-%Y-%H-%M-%S")
                            string_index = "Time:" + dt_string + ", Scenario: " + scenario_testcase["ScenarioName"] + \
                                           ", Bug: " + str(output_trace["testFailures"]) + '\n'
                            bug_file.write(string_index)
                            json.dump(output_trace, bug_file, indent=2)
                            bug_file.write('\n')
                    monitor = Monitor(output_trace, 0)
                    for spec in specs:
                        if spec in covered_specs:
                            continue
                        robustness = monitor.continuous_monitor2(spec)
                        reward[specs_table[spec]] = robustness
                        if robustness < 0.0:
                            continue
                        covered_specs.append(spec)
                        with open(directory + '/violationTestCase.txt', 'a') as violation_file:
                            now = datetime.now()
                            dt_string = now.strftime("%d-%m-%Y-%H-%M-%S")
                            string_index = "Time:" + dt_string + ". Scenario: " + scenario_testcase["ScenarioName"] + '\n'
                            violation_file.write(string_index)
                            string_index2 = "The detailed fitness values:" + str(robustness) + '\n'
                            violation_file.write(string_index2)
                            # bug_file.write(spec)
                            json.dump(output_trace, violation_file, indent=2)
                            violation_file.write('\n')

                elif len(output_trace['trace']) == 1:
                    logging.info("Only one state. Is reached: {}, minimal distance: {}".format(
                        output_trace['destinationReached'], output_trace['minEgoObsDist']))
                else:
                    logging.info("No trace for the test cases!")
                    with open(directory + '/NoTrace.txt', 'a') as f:
                        now = datetime.now()
                        dt_string = now.strftime("%d-%m-%Y-%H-%M-%S")
                        f.write("Time: {}, Scenario: {}".format(dt_string, scenario_testcase["ScenarioName"]))
                        json.dump(scenario_testcase, f, indent=2)
                        f.write('\n')
                init_msg = json.dumps({'CMD': "CMD_READY_FOR_NEW_TEST"})
                await websocket.send(init_msg)
                break
            else:
                print("Incorrect response.")
                break


def get_history_scenarios(session):
    with open(path_args.train_data_path.format(session)) as file:
        dataset = json.load(file)
    return dataset


def generate_scenarios_batch(session, test_cases_for_training):
    # Train model to generate new scenarios to files
    train_proxy(proxy_args, session)
    generate_samples_with_gfn(session)
    #
    test_cases_batch = []
    with open(path_args.result_path.format(session)) as file:
        dataset = json.load(file)
        for item in dataset:
            test_cases_batch.append(decode(item, session))
    return test_cases_batch


"""
For debugging single scenario
"""
def generate_one_scenario():
    test_cases = []
    one_testcase = "generator/data/one_testcase.json"
    with open(one_testcase) as file:
        test_cases.append(json.load(file))
    return test_cases


def load_specifications():
    with open(path_args.spec_path) as file:
        specs = json.load(file)
    del specs["all_rules"]
    table = dict()
    for idx, spec in enumerate(specs.values()):
        table[spec] = idx + 1
    return list(specs.values()), table


def test_scenario_batch(testcases, remained_specs, file_directory):
    covered_specs = list()
    new_dataset = []
    # print("Uncovered specs before batch {}: {}".format(batch_no, len(remain_specs)))
    for item in testcases:
        reward = [-100000.0] * 82
        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            asyncio.gather(asyncio.gather(
                test_one_scenario(item, remained_specs, covered_specs, reward, directory=file_directory))))
        item["robustness"] = reward
        logging.info("Current covered specs: {} until the scenario {}".format(len(covered_specs), item['ScenarioName']))
        new_dataset.append(item)
    # update dataset
    for cs in covered_specs:
        remained_specs.remove(cs)
    # print("Uncovered specs after batch {}: {}".format(batch_no, len(remain_specs)))
    return covered_specs, new_dataset


def update_dataset(history_data_for_training, batch_testdata, remained_specs):
    result = list()
    # update robustness values
    for item in history_data_for_training:
        usable_robust = []
        for remain in remained_specs:
            usable_robust.append(item['robustness'][specs_table[remain]])
        item['robustness'][0] = -max(usable_robust)
    result.extend(history_data_for_training)
    # merge newly-generated scenarios
    idx = 0
    for item in batch_testdata:
        ScenarioName = item["ScenarioName"] + "_" + str(idx)
        item['robustness'][0] = -max(list(item['robustness']))
        action_seq = encode(item)
        action_seq["ScenarioName"] = ScenarioName
        # tmp = {"ScenarioName":ScenarioName, "actions":action_seq["actions"], "robustness":item['robustness']}
        result.append(action_seq)
        idx += 1
    return result


def test_session(session, total_specs_num, remained_specs):
    log_direct = path_args.test_result_direct.format(session)
    # Set testing result or data paths
    if not os.path.exists(log_direct):
        os.makedirs(log_direct)
    else:
        shutil.rmtree(log_direct)
    if not os.path.exists(log_direct + '/data'):
        os.makedirs(log_direct + '/data')
    # Set logger
    logging_file = log_direct + '/test.log'
    file_handler = logging.FileHandler(logging_file, mode='w')
    stdout_handler = logging.StreamHandler(sys.stdout)
    logging.basicConfig(level=logging.INFO, handlers=[stdout_handler, file_handler],
                        format='%(asctime)s, %(levelname)s: %(message)s', datefmt="%Y-%m-%d %H:%M:%S")
    logging.info("Current Session: {}".format(session))
    # Initialize Batch_0
    history_data_for_training = get_history_scenarios(session)
    # Active learning loop
    covered_specs = list()
    for b_index in range(batch_size):
        new_testcase_batch = generate_scenarios_batch(session, history_data_for_training)
        # new_testcase_batch = generate_one_scenario()
        batch_covered_specs, batch_testdata = test_scenario_batch(new_testcase_batch, remained_specs, log_direct)
        coverage_rate = 1 - len(remained_specs) / total_specs_num
        logging.info("Batch: {}, generating new testcases: {}, total coverage rate: {}/{} = {}, "
                     "new covered predicates: {}\n".format(b_index, len(new_testcase_batch),
                                                           (total_specs_num - len(remained_specs)),
                                                           total_specs_num, coverage_rate, batch_covered_specs))
        covered_specs.extend(batch_covered_specs)
        history_data_for_training = update_dataset(history_data_for_training, batch_testdata, remained_specs)
        dataset_path = path_args.result_path.format(session)
        with open(dataset_path, 'w') as wf:
            json.dump(history_data_for_training, wf, indent=4)
    return covered_specs

specs_table = dict()
batch_size = 1

if __name__ == "__main__":
    # sessions = ['double_direction', 'single_direction', 'lane_change']
    sessions = ['single_direction']
    # all_specs and specs_table have the same ordering for each spec
    all_specs, specs_table = load_specifications()
    total_specs_num = len(all_specs)
    all_covered_specs = list()
    for session in sessions:
        session_covered_specs = test_session(session, total_specs_num, all_specs)
        all_covered_specs.extend(session_covered_specs)
        logging.info("Session: {}, total coverage rate: {}/{} = {}, new covered predicates: {}\n".format(session,
           len(all_covered_specs), total_specs_num, len(all_covered_specs) / total_specs_num, session_covered_specs))
    #
    print("Finished, total coverage rate: {}/{} = {}, the covered predicates: {}\n".format(
        len(all_covered_specs), total_specs_num, len(all_covered_specs)/total_specs_num, all_covered_specs))