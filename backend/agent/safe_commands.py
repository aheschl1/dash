"""Strict allowlist classifier for read-only root_bash commands.

Lets root_bash skip the human-approval prompt when a command is provably
incapable of changing system state. Fail-closed: anything not explicitly
recognized as read-only still requires approval like any other root_bash
call — this only ever *removes* a prompt, never grants new capability
(cat_file/ls_folder already expose root reads with zero approval).

Disable wholesale with AGENT_BASH_AUTO_APPROVE=0 (every root_bash call then
prompts, regardless of content).
"""
import os
import shlex

# Binaries that are read-only no matter what arguments follow.
_PURE_READ = {
    "cat", "less", "more", "head", "tail", "wc", "grep", "egrep", "fgrep",
    "ls", "stat", "file", "pwd", "whoami", "id", "uname", "hostname",
    "uptime", "free", "df", "du", "ps", "top", "lscpu", "lsblk", "lsusb",
    "lspci", "nproc", "env", "printenv", "echo", "which", "type",
    "ss", "ping", "dig", "nslookup", "host", "w", "who", "last",
    "sha256sum", "md5sum", "basename", "dirname", "sort", "uniq", "cut",
    "tr", "diff", "comm", "column", "tree", "realpath", "true", "sensors",
}

_FIND_UNSAFE_FLAGS = {
    "-exec", "-execdir", "-ok", "-okdir", "-delete",
    "-fls", "-fprint", "-fprint0", "-fprintf",
}


def _validate_find(args: list[str]) -> bool:
    return not any(a in _FIND_UNSAFE_FLAGS for a in args)


_DOCKER_SAFE_SUBCOMMANDS = {
    "ps", "images", "inspect", "logs", "top", "stats", "version", "info",
    "port", "diff", "history", "events",
}


def _validate_docker(args: list[str]) -> bool:
    return bool(args) and args[0] in _DOCKER_SAFE_SUBCOMMANDS


_SYSTEMCTL_SAFE_SUBCOMMANDS = {
    "status", "show", "is-active", "is-enabled", "is-failed",
    "list-units", "list-unit-files", "list-jobs", "list-timers",
    "list-sockets", "cat",
}


def _validate_systemctl(args: list[str]) -> bool:
    return bool(args) and args[0] in _SYSTEMCTL_SAFE_SUBCOMMANDS


_IP_MUTATING_VERBS = {
    "add", "del", "delete", "change", "replace", "set", "flush",
    "append", "prepend", "save", "restore",
}


def _validate_ip(args: list[str]) -> bool:
    return not any(a in _IP_MUTATING_VERBS for a in args)


_VIRSH_SAFE_SUBCOMMANDS = {
    "list", "dominfo", "domstate", "domblklist", "domiflist", "nodeinfo",
    "version", "capabilities", "dumpxml", "vcpuinfo", "domuuid",
    "net-list", "pool-list", "dommemstat",
}


def _validate_virsh(args: list[str]) -> bool:
    sub = next((a for a in args if not a.startswith("-") and "://" not in a), None)
    return sub in _VIRSH_SAFE_SUBCOMMANDS


_DATE_UNSAFE_FLAGS = {"-s", "--set"}


def _validate_date(args: list[str]) -> bool:
    return not any(a in _DATE_UNSAFE_FLAGS or a.startswith("--set=") for a in args)


_DMESG_UNSAFE_FLAGS = {"-c", "-C", "--clear", "--read-clear"}


def _validate_dmesg(args: list[str]) -> bool:
    return not any(a in _DMESG_UNSAFE_FLAGS for a in args)


_JOURNALCTL_UNSAFE_FLAGS = {"--rotate", "--flush", "--sync", "--relinquish-var"}


def _validate_journalctl(args: list[str]) -> bool:
    return not any(
        a in _JOURNALCTL_UNSAFE_FLAGS or a.startswith("--vacuum-") for a in args
    )


_NVIDIA_SMI_SAFE_PREFIXES = ("-q", "-L", "-h", "--query", "--format", "--help", "-d")


def _validate_nvidia_smi(args: list[str]) -> bool:
    return all(a.startswith(_NVIDIA_SMI_SAFE_PREFIXES) for a in args)


_SMARTCTL_UNSAFE_FLAGS = {"-t", "--test", "-X", "--abort"}


def _validate_smartctl(args: list[str]) -> bool:
    return not any(a in _SMARTCTL_UNSAFE_FLAGS or a.startswith("--test=") for a in args)


_VALIDATORS = {
    "find": _validate_find,
    "docker": _validate_docker,
    "systemctl": _validate_systemctl,
    "ip": _validate_ip,
    "virsh": _validate_virsh,
    "date": _validate_date,
    "dmesg": _validate_dmesg,
    "journalctl": _validate_journalctl,
    "nvidia-smi": _validate_nvidia_smi,
    "smartctl": _validate_smartctl,
}

_SEPARATORS = {"|", ";", "&&", "||"}


def _is_safe_simple_command(tokens: list[str]) -> bool:
    if not tokens:
        return False
    binary = tokens[0].rsplit("/", 1)[-1]
    if binary in _PURE_READ:
        return True
    validator = _VALIDATORS.get(binary)
    if validator is None:
        return False
    return validator(tokens[1:])


def is_safe_command(command: str) -> bool:
    if os.environ.get("AGENT_BASH_AUTO_APPROVE", "1") == "0":
        return False

    command = (command or "").strip()
    if not command:
        return False

    # Fail closed on anything that can hide a mutation behind read-looking
    # syntax: command/process substitution, here-docs, redirection.
    if any(tok in command for tok in ("$(", "`", "<(", ">(", "<<")):
        return False
    if "\n" in command or "\x00" in command:
        return False

    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return False  # unbalanced quotes — can't reason about it safely
    if not tokens:
        return False

    segments: list[list[str]] = [[]]
    for tok in tokens:
        if tok in _SEPARATORS:
            if not segments[-1]:
                return False  # leading/doubled separator
            segments.append([])
        elif tok == "&":
            return False  # backgrounding
        elif any(ch in tok for ch in "><"):
            return False  # redirection glued to a token with no surrounding space
        else:
            segments[-1].append(tok)
    if not segments[-1]:
        return False  # trailing separator

    return all(_is_safe_simple_command(seg) for seg in segments)
