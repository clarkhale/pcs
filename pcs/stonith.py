from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals,
)

import json
import sys

from pcs import (
    resource,
    usage,
    utils,
)
from pcs.cli.common import parse_args
from pcs.cli.common.console_report import indent
from pcs.cli.common.errors import CmdLineInputError
from pcs.cli.fencing_topology import target_type_map_cli_to_lib
from pcs.common import report_codes
from pcs.common.fencing_topology import (
    TARGET_TYPE_NODE,
    TARGET_TYPE_REGEXP,
    TARGET_TYPE_ATTRIBUTE,
)
from pcs.lib.errors import LibraryError, ReportItemSeverity
import pcs.lib.resource_agent as lib_ra

def stonith_cmd(argv):
    if len(argv) < 1:
        sub_cmd, argv_next = "show", []
    else:
        sub_cmd, argv_next = argv[0], argv[1:]

    lib = utils.get_library_wrapper()
    modifiers = utils.get_modificators()

    try:
        if sub_cmd == "help":
            usage.stonith(argv)
        elif sub_cmd == "list":
            stonith_list_available(lib, argv_next, modifiers)
        elif sub_cmd == "describe":
            stonith_list_options(lib, argv_next, modifiers)
        elif sub_cmd == "create":
            stonith_create(argv_next)
        elif sub_cmd == "update":
            if len(argv_next) > 1:
                stn_id = argv_next.pop(0)
                resource.resource_update(stn_id, argv_next)
            else:
                raise CmdLineInputError()
        elif sub_cmd == "delete":
            if len(argv_next) == 1:
                stn_id = argv_next.pop(0)
                resource.resource_remove(stn_id)
            else:
                raise CmdLineInputError()
        elif sub_cmd == "show":
            resource.resource_show(argv_next, True)
            levels = stonith_level_config_to_str(
                lib.fencing_topology.get_config()
            )
            if levels:
                print("\n".join(indent(levels, 1)))
        elif sub_cmd == "level":
            stonith_level_cmd(lib, argv_next, modifiers)
        elif sub_cmd == "fence":
            stonith_fence(argv_next)
        elif sub_cmd == "cleanup":
            resource.resource_cleanup(argv_next)
        elif sub_cmd == "confirm":
            stonith_confirm(argv_next)
        elif sub_cmd == "get_fence_agent_info":
            get_fence_agent_info(argv_next)
        elif sub_cmd == "sbd":
            sbd_cmd(lib, argv_next, modifiers)
        else:
            raise CmdLineInputError()
    except LibraryError as e:
        utils.process_library_reports(e.args)
    except CmdLineInputError as e:
        utils.exit_on_cmdline_input_errror(e, "stonith", sub_cmd)

def stonith_level_cmd(lib, argv, modifiers):
    if len(argv) < 1:
        sub_cmd, argv_next = "config", []
    else:
        sub_cmd, argv_next = argv[0], argv[1:]

    try:
        if sub_cmd == "add":
            stonith_level_add_cmd(lib, argv_next, modifiers)
        elif sub_cmd == "clear":
            stonith_level_clear_cmd(lib, argv_next, modifiers)
        elif sub_cmd == "config":
            stonith_level_config_cmd(lib, argv_next, modifiers)
        elif sub_cmd in ["remove", "delete"]:
            stonith_level_remove_cmd(lib, argv_next, modifiers)
        elif sub_cmd == "verify":
            stonith_level_verify_cmd(lib, argv_next, modifiers)
        else:
            sub_cmd = ""
            raise CmdLineInputError()
    except CmdLineInputError as e:
        utils.exit_on_cmdline_input_errror(
            e, "stonith", "level {0}".format(sub_cmd)
        )

def stonith_list_available(lib, argv, modifiers):
    if len(argv) > 1:
        raise CmdLineInputError()

    search = argv[0] if argv else None
    agent_list = lib.stonith_agent.list_agents(modifiers["describe"], search)

    if not agent_list:
        if search:
            utils.err("No stonith agents matching the filter.")
        utils.err(
            "No stonith agents available. "
            "Do you have fence agents installed?"
        )

    for agent_info in agent_list:
        name = agent_info["name"]
        shortdesc = agent_info["shortdesc"]
        if shortdesc:
            print("{0} - {1}".format(
                name,
                resource._format_desc(
                    len(name + " - "), shortdesc.replace("\n", " ")
                )
            ))
        else:
            print(name)


def stonith_list_options(lib, argv, modifiers):
    if len(argv) != 1:
        raise CmdLineInputError()
    agent_name = argv[0]

    print(resource._format_agent_description(
        lib.stonith_agent.describe_agent(agent_name),
        True
    ))


def stonith_create(argv):
    if len(argv) < 2:
        usage.stonith(["create"])
        sys.exit(1)

    stonith_id = argv.pop(0)
    stonith_type = argv.pop(0)
    st_values, op_values, meta_values = resource.parse_resource_options(
        argv, with_clone=False
    )

    try:
        metadata = lib_ra.StonithAgent(
            utils.cmd_runner(),
            stonith_type
        )
        if metadata.get_provides_unfencing():
            meta_values = [
                meta for meta in meta_values if not meta.startswith("provides=")
            ]
            meta_values.append("provides=unfencing")
    except lib_ra.ResourceAgentError as e:
        forced = utils.get_modificators().get("force", False)
        if forced:
            severity = ReportItemSeverity.WARNING
        else:
            severity = ReportItemSeverity.ERROR
        utils.process_library_reports([
            lib_ra.resource_agent_error_to_report_item(
                e, severity, not forced
            )
        ])
    except LibraryError as e:
        utils.process_library_reports(e.args)

    resource.resource_create(
        stonith_id, "stonith:" + stonith_type, st_values, op_values, meta_values,
        group=utils.pcs_options.get("--group", None)
    )

def stonith_level_parse_node(arg):
    target_type_candidate, target_value_candidate = parse_args.parse_typed_arg(
        arg,
        target_type_map_cli_to_lib.keys(),
        "node"
    )
    target_type = target_type_map_cli_to_lib[target_type_candidate]
    if target_type == TARGET_TYPE_ATTRIBUTE:
        target_value = parse_args.split_option(target_value_candidate)
    else:
        target_value = target_value_candidate
    return target_type, target_value

def stonith_level_normalize_devices(argv):
    # normalize devices - previously it was possible to delimit devices by both
    # a comma and a space
    return ",".join(argv).split(",")

def stonith_level_add_cmd(lib, argv, modifiers):
    if len(argv) < 3:
        raise CmdLineInputError()
    target_type, target_value = stonith_level_parse_node(argv[1])
    lib.fencing_topology.add_level(
        argv[0],
        target_type,
        target_value,
        stonith_level_normalize_devices(argv[2:]),
        force_device=modifiers["force"],
        force_node=modifiers["force"]
    )

def stonith_level_clear_cmd(lib, argv, modifiers):
    if len(argv) > 1:
        raise CmdLineInputError()

    if not argv:
        lib.fencing_topology.remove_all_levels()
        return

    target_type, target_value = stonith_level_parse_node(argv[0])
    # backward compatibility mode
    # Command parameters are: node, stonith-list
    # Both the node and the stonith list are optional. If the node is ommited
    # and the stonith list is present, there is no way to figure it out, since
    # there is no specification of what the parameter is. Hence the pre-lib
    # code tried both. It deleted all levels having the first parameter as
    # either a node or a device list. Since it was only possible to specify
    # node as a target back then, this is enabled only in that case.
    report_item_list = []
    try:
        lib.fencing_topology.remove_levels_by_params(
            None,
            target_type,
            target_value,
            None,
            # pre-lib code didn't return any error when no level was found
            ignore_if_missing=True
        )
    except LibraryError as e:
        report_item_list.extend(e.args)
    if target_type == TARGET_TYPE_NODE:
        try:
            lib.fencing_topology.remove_levels_by_params(
                None,
                None,
                None,
                argv[0].split(","),
                # pre-lib code didn't return any error when no level was found
                ignore_if_missing=True
            )
        except LibraryError as e:
            report_item_list.extend(e.args)
    if report_item_list:
        raise LibraryError(*report_item_list)

def stonith_level_config_to_str(config):
    config_data = dict()
    for level in config:
        if level["target_type"] not in config_data:
            config_data[level["target_type"]] = dict()
        if level["target_value"] not in config_data[level["target_type"]]:
            config_data[level["target_type"]][level["target_value"]] = []
        config_data[level["target_type"]][level["target_value"]].append(level)

    lines = []
    for target_type in [
        TARGET_TYPE_NODE, TARGET_TYPE_REGEXP, TARGET_TYPE_ATTRIBUTE
    ]:
        if not target_type in config_data:
            continue
        for target_value in sorted(config_data[target_type].keys()):
            lines.append("Target: {0}".format(
                "=".join(target_value) if target_type == TARGET_TYPE_ATTRIBUTE
                else target_value
            ))
            level_lines = []
            for target_level in sorted(
                config_data[target_type][target_value],
                key=lambda level: level["level"]
            ):
                level_lines.append("Level {level} - {devices}".format(
                    level=target_level["level"],
                    devices=",".join(target_level["devices"])
                ))
            lines.extend(indent(level_lines))
    return lines

def stonith_level_config_cmd(lib, argv, modifiers):
    if len(argv) > 0:
        raise CmdLineInputError()
    lines = stonith_level_config_to_str(lib.fencing_topology.get_config())
    # do not print \n when lines are empty
    if lines:
        print("\n".join(lines))

def stonith_level_remove_cmd(lib, argv, modifiers):
    if len(argv) < 1:
        raise CmdLineInputError()
    target_type, target_value, devices = None, None, None
    level = argv[0]
    if len(argv) > 1:
        target_type, target_value = stonith_level_parse_node(argv[1])
    if len(argv) > 2:
        devices = stonith_level_normalize_devices(argv[2:])

    try:
        lib.fencing_topology.remove_levels_by_params(
            level,
            target_type,
            target_value,
            devices
        )
    except LibraryError as e:
        # backward compatibility mode
        # Command parameters are: level, node, stonith, stonith...
        # Both the node and the stonith list are optional. If the node is
        # ommited and the stonith list is present, there is no way to figure it
        # out, since there is no specification of what the parameter is. Hence
        # the pre-lib code tried both. First it assumed the first parameter is
        # a node. If that fence level didn't exist, it assumed the first
        # parameter is a device. Since it was only possible to specify node as
        # a target back then, this is enabled only in that case.
        if target_type != TARGET_TYPE_NODE:
            raise e
        level_not_found = False
        for report_item in e.args:
            if (
                report_item.code
                ==
                report_codes.CIB_FENCING_LEVEL_DOES_NOT_EXIST
            ):
                level_not_found = True
                break
        if not level_not_found:
            raise e
        target_and_devices = [target_value]
        if devices:
            target_and_devices.extend(devices)
        try:
            lib.fencing_topology.remove_levels_by_params(
                level,
                None,
                None,
                target_and_devices
            )
        except LibraryError as e_second:
            raise LibraryError(*(e.args + e_second.args))

def stonith_level_verify_cmd(lib, argv, modifiers):
    if len(argv) > 0:
        raise CmdLineInputError()
    # raises LibraryError in case of problems, else we don't want to do anything
    lib.fencing_topology.verify()

def stonith_fence(argv):
    if len(argv) != 1:
        utils.err("must specify one (and only one) node to fence")

    node = argv.pop(0)
    if "--off" in utils.pcs_options:
        args = ["stonith_admin", "-F", node]
    else:
        args = ["stonith_admin", "-B", node]
    output, retval = utils.run(args)

    if retval != 0:
        utils.err("unable to fence '%s'\n" % node + output)
    else:
        print("Node: %s fenced" % node)

def stonith_confirm(argv, skip_question=False):
    if len(argv) != 1:
        utils.err("must specify one (and only one) node to confirm fenced")

    node = argv.pop(0)
    if not skip_question and "--force" not in utils.pcs_options:
        answer = utils.get_terminal_input(
            (
                "WARNING: If node {node} is not powered off or it does"
                + " have access to shared resources, data corruption and/or"
                + " cluster failure may occur. Are you sure you want to"
                + " continue? [y/N] "
            ).format(node=node)
        )
        if answer.lower() not in ["y", "yes"]:
            print("Canceled")
            return
    args = ["stonith_admin", "-C", node]
    output, retval = utils.run(args)

    if retval != 0:
        utils.err("unable to confirm fencing of node '%s'\n" % node + output)
    else:
        print("Node: %s confirmed fenced" % node)


def get_fence_agent_info(argv):
# This is used only by pcsd, will be removed in new architecture
    if len(argv) != 1:
        utils.err("One parameter expected")

    agent = argv[0]
    if not agent.startswith("stonith:"):
        utils.err("Invalid fence agent name")

    runner = utils.cmd_runner()

    try:
        metadata = lib_ra.StonithAgent(runner, agent[len("stonith:"):])
        info = metadata.get_full_info()
        info["name"] = "stonith:{0}".format(info["name"])
        print(json.dumps(info))
    except lib_ra.ResourceAgentError as e:
        utils.process_library_reports(
            [lib_ra.resource_agent_error_to_report_item(e)]
        )
    except LibraryError as e:
        utils.process_library_reports(e.args)


def sbd_cmd(lib, argv, modifiers):
    if len(argv) == 0:
        raise CmdLineInputError()
    cmd = argv.pop(0)
    try:
        if cmd == "enable":
            sbd_enable(lib, argv, modifiers)
        elif cmd == "disable":
            sbd_disable(lib, argv, modifiers)
        elif cmd == "status":
            sbd_status(lib, argv, modifiers)
        elif cmd == "config":
            sbd_config(lib, argv, modifiers)
        elif cmd == "local_config_in_json":
            local_sbd_config(lib, argv, modifiers)
        else:
            raise CmdLineInputError()
    except CmdLineInputError as e:
        utils.exit_on_cmdline_input_errror(
            e, "stonith", "sbd {0}".format(cmd)
        )


def sbd_enable(lib, argv, modifiers):
    sbd_cfg = parse_args.prepare_options(argv)
    default_watchdog, watchdog_dict = _sbd_parse_watchdogs(
        modifiers["watchdog"]
    )
    lib.sbd.enable_sbd(
        default_watchdog,
        watchdog_dict,
        sbd_cfg,
        allow_unknown_opts=modifiers["force"],
        ignore_offline_nodes=modifiers["skip_offline_nodes"]
    )


def _sbd_parse_watchdogs(watchdog_list):
    default_watchdog = None
    watchdog_dict = {}

    for watchdog_node in watchdog_list:
        if "@" not in watchdog_node:
            if default_watchdog:
                raise CmdLineInputError("Multiple watchdog definitions.")
            default_watchdog = watchdog_node
        else:
            watchdog, node_name = watchdog_node.rsplit("@", 1)
            if node_name in watchdog_dict:
                raise CmdLineInputError(
                    "Multiple watchdog definitions for node '{node}'".format(
                        node=node_name
                    )
                )
            watchdog_dict[node_name] = watchdog

    return default_watchdog, watchdog_dict


def sbd_disable(lib, argv, modifiers):
    if argv:
        raise CmdLineInputError()

    lib.sbd.disable_sbd(modifiers["skip_offline_nodes"])


def sbd_status(lib, argv, modifiers):
    def _bool_to_str(val):
        if val is None:
            return "N/A"
        return "YES" if val else " NO"

    if argv:
        raise CmdLineInputError()

    status_list = lib.sbd.get_cluster_sbd_status()
    if not len(status_list):
        utils.err("Unable to get SBD status from any node.")

    print("SBD STATUS")
    print("<node name>: <installed> | <enabled> | <running>")
    for node_status in status_list:
        status = node_status["status"]
        print("{node}: {installed} | {enabled} | {running}".format(
            node=node_status["node"].label,
            installed=_bool_to_str(status.get("installed")),
            enabled=_bool_to_str(status.get("enabled")),
            running=_bool_to_str(status.get("running"))
        ))


def sbd_config(lib, argv, modifiers):
    if argv:
        raise CmdLineInputError()

    config_list = lib.sbd.get_cluster_sbd_config()

    if not config_list:
        utils.err("No config obtained.")

    config = config_list[0]["config"]

    filtered_options = ["SBD_WATCHDOG_DEV", "SBD_OPTS", "SBD_PACEMAKER"]
    for key, val in config.items():
        if key in filtered_options:
            continue
        print("{key}={val}".format(key=key, val=val))

    print()
    print("Watchdogs:")
    for config in config_list:
        watchdog = "<unknown>"
        if config["config"] is not None:
            watchdog = config["config"].get("SBD_WATCHDOG_DEV", "<unknown>")
        print("  {node}: {watchdog}".format(
            node=config["node"].label,
            watchdog=watchdog
        ))


def local_sbd_config(lib, argv, modifiers):
    print(json.dumps(lib.sbd.get_local_sbd_config()))
