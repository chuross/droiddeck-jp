"""Record selected Proton identity without modifying third-party sources."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HELPERS = ("droiddeck-steam-diag-check", "droiddeck-log-analyze")
ASSETS = tuple("usr/local/bin/" + name for name in HELPERS)
HOOKS = tuple(indent + 'os.execvpe(launch[0], launch, launch_env)\n'
              for indent in ('            ', '    '))
FUNCTION = '''def steam_diag_provenance(command, env):
    if env.get("PROTON_LOG") != "1":
        return
    try:
        subprocess.run(["/usr/local/bin/droiddeck-steam-diag-check", command[0]],
                       check=False, env=env)
    except OSError as error:
        print("STEAMDIAG: could not identify selected Proton (%s)" % error, file=sys.stderr)


'''


def script(source):
    return source / "tools/linuxfs/overlay/usr/local/bin/droiddeck-game-env"


def check(source):
    text = script(source).read_text()
    if any(text.splitlines(keepends=True).count(hook) != 1 for hook in HOOKS) or text.count("def main():") != 1:
        raise RuntimeError("Steam diagnostics launch hook changed; review overlay integration")


def apply(source):
    check(source)
    target = script(source)
    text = target.read_text().replace("def main():", FUNCTION + "def main():")
    lines = text.splitlines(keepends=True)
    for hook in HOOKS:
        index = lines.index(hook)
        indent = hook[:len(hook) - len(hook.lstrip())]
        lines.insert(index, indent + 'steam_diag_provenance(launch, launch_env)\n')
    text = ''.join(lines)
    target.write_text(text)

