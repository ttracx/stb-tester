#!/bin/bash

# WARNING: DON'T ADD SUPPORT FOR NEW POWER DEVICES TO THIS SCRIPT.
#
# Add them to _stbt/power.py instead.
#
# If you own one of the devices supported by this script please consider
# porting the implementation to Python, and writing some tests for that
# implementation.

#/ usage: stbt power [--help] [--power-outlet <uri>] on|off|status
#/
#/ Send commands to network-controllable power switch.
#/
#/ Options:
#/   -h, --help    Print this help message and exit.
#/   --power-outlet <uri>
#/                 Address of the power device and the outlet on the device.
#/                 The format of <uri> is: (ipp|pdu):<hostname>:<outlet>
#/                   ipp|pdu       Model of the controllable power supply:
#/                                 * ipp: IP Power 9258
#/                                 * pdu: PDUeX KWX
#/                   <hostname>    The device's network address.
#/                   <outlet>      Address of the individual power outlet on
#/                                 the device. Allowed values depend on the
#/                                 specific device model. Optional for the
#/                                 "status" command.
#/                 Taken from stbt.conf's "global.power_outlet" if not
#/                 specified on the command line.

set -o pipefail

usage() { grep '^#/' "$0" | cut -c4-; }  # Print the above usage message.
die() { echo "stbt power: error: $*" >&2; exit 1; }

main() {
    [ $# -eq 0 ] && { usage >&2; exit 1; }
    while [ $# -gt 0 ]; do
        case "$1" in
            -h|--help) usage; exit 0;;
            --power-outlet) uri="$2"; shift;;
            --power-outlet=*) uri="${1#--power-outlet=}";;
            *) break;;
        esac
        shift
    done
    command="$1"
    [[ "$command" =~ ^(on|off|status)$ ]] || die "invalid command '$command'"

    [[ -z "$uri" ]] && {
        uri=$("$(dirname "$0")"/stbt_config.py "global.power_outlet" 2>/dev/null) ||
            die "no power-outlet specified on command line or in config file"
    }
    model=$(uri model "$uri") || die "invalid power-outlet uri '$uri'"
    hostname=$(uri hostname "$uri") || die "invalid power-outlet uri '$uri'"
    outlet=$(uri outlet "$uri") || die "invalid power-outlet uri '$uri'"
    [[ -z "$outlet" && $command != status ]] &&
        die "missing outlet from uri '$uri'"

    $model $command $hostname "$outlet"
}

uri() {
    local regex='^(?<model>pdu|ipp|testfallback):(?<hostname>[^: ]+)(:(?<outlet>[^: ]+))?$'
    echo "$2" | perl -ne \
        "if (/$regex/) { print $+{$1} ? $+{$1} : ''; }
         else { exit 1; }"
}

testfallback() {
    local command=$1 hostname=$2 outlet=$3 status

    case $command in
        on) return 0 ;;
        off) return 0 ;;
        status) echo ON && exit 0 ;;
        *) exit 1
    esac
}

ipp() {
    local command=$1 hostname=$2 outlet="$3" output

    output=$(
        curl --silent --fail http://admin:12345678@$hostname/Set.cmd?CMD=$(
            ipp_command $command "$outlet")
    ) || die "failed to connect to '$hostname'"

    echo "$output" | grep -q BADPARAM &&
        die "invalid outlet '$outlet' (hint: use the 'status' command)"

    # Prettify the output from the device
    echo "$output" |
    sed 's|</*html>||g' |
    tr "," "\n" |
    sed -e 's|=1| = ON|g' -e 's|=0| = OFF|g' |
    ipp_filteroutlet "$outlet" |
    head -4  # IP Power device prints 8 outlet names, but only has 4.
}
ipp_command() {
    local command=$1 outlet="$2"
    case "$1" in
        on) echo "SetPower+$outlet=1";;
        off) echo "SetPower+$outlet=0";;
        status) echo "GetPower";;
    esac
}
ipp_filteroutlet() {
    local outlet="$1"
    if [ -z "$outlet" ]; then
        cat
    else
        grep "$outlet" ||
            die "invalid outlet '$outlet' (hint: use the 'status' command)"
    fi
}

pdu() {
    local command=$1 hostname=$2 outlet="$3" pdu_error command_ids data _

    pdu_error="Failed to execute command '$command' on PDU '$hostname' "
    pdu_error+="outlet '${outlet:-all}'"

    if [[ $command == status ]]; then
        if [[ -n $outlet ]]; then
            pdu_status $hostname "$outlet" || die "$pdu_error"
        else
            { pdu_status $hostname '1-A[0-9]+' || die "$pdu_error"; } | \
                awk '{ printf("1-A%d: %s\n", NR, $0) }'  # Add outlet addresses
        fi
    else
        declare -A command_ids=([on]=1 [off]=2)

        # 2 attempts to minimise failure rate
        for _ in 1 2; do
            data="selOutlet=%3F$(pdu_outlet_no "$outlet")&"
            data+="ctrl_all=${command_ids[$command]}"
            _curl -d "$data" \
                http://admin:admin@$hostname/Forms/index_3 >/dev/null

            # The above HTTP POST always succeeds, but doesn't always switch
            # the power outlet. Check by querying the new outlet status,
            # allowing up to 8 seconds for it to take effect.
            for _ in {1..8}; do
                sleep 1
                pdu_status $hostname "$outlet" | grep -iq "$command" && return
            done
        done

        die "$pdu_error"
    fi
}
pdu_status() {
    local hostname=$1 outlet="$2"
    _curl http://admin:admin@$hostname | awk "/$outlet/,/<\/ul>/" | \
        grep -Eo 'ON|OFF'
}
pdu_outlet_no() {
    echo $1 | sed 's/^1-A//'
}
_curl() {
    # 2 attempts to minimise failure rate
    curl --anyauth --fail --silent "$@" ||
    curl --anyauth --fail --show-error --silent "$@"
}

main "$@"
