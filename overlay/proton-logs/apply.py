"""Save Proton logs in the current session directory."""

def patch_proton_logs(source):
    script = source / "tools/linuxfs/overlay/usr/local/bin/droiddeck-game-env"
    text = script.read_text()
    replacements = {
        "def main():": '''def session_proton_logs(env):
    # Proton controls whether to log; only choose the current session's folder.
    directory = env.get("BL_DEBUG_DIR") or os.path.dirname(env.get("BL_LOG", ""))
    if directory and os.path.isabs(directory):
        return dict(env, PROTON_LOG_DIR=directory)
    return env


def main():''',
        "    launch, launch_env = select_sync(command, env, sync)":
            "    launch, launch_env = select_sync(command, env, sync)\n"
            "    launch_env = session_proton_logs(launch_env)",
        "            record_stock(sync, launch, launch_env)":
            "            launch_env = session_proton_logs(launch_env)\n"
            "            record_stock(sync, launch, launch_env)",
    }
    for before, after in replacements.items():
        if text.count(before) != 1:
            raise RuntimeError("droiddeck-game-env changed; review session Proton log integration")
        text = text.replace(before, after)
    script.write_text(text)

def apply(source):
    patch_proton_logs(source)
