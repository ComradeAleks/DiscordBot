import asyncio
import glob
import logging
import os
import sys

logger = logging.getLogger(__name__)

REPO_DIR = os.path.dirname(os.path.abspath(__file__))

# The repo is public, so updates are downloaded from here without logging in,
# no matter how git is set up on this machine
REPO_URL = "https://github.com/ComradeAleks/DiscordBot.git"
BRANCH = "main"

# When to check GitHub for updates every day (Europe/Oslo)
UPDATE_TIME = {"hour": 6, "minute": 0}


async def run_git(*args):
    """Run a git command in the bot folder.

    Returns:
        tuple: (bool: success, str: output)
    """
    # Never wait for a username/password prompt or a saved-password helper, fail instead
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    process = await asyncio.create_subprocess_exec(
        "git", "-c", "credential.helper=", *args,
        cwd=REPO_DIR,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=120)
    except asyncio.TimeoutError:
        process.kill()
        return False, f"git {args[0]} timed out"
    return process.returncode == 0, output.decode(errors="replace").strip()


async def check_for_updates(bot):
    """Download new code from GitHub and restart the bot. The JSON data files (settings, history, movies) are kept as they are."""
    try:
        logger.info("Checking GitHub for updates")

        # Also updates origin/main, so "git status" shows the right thing
        success, output = await run_git("fetch", "--quiet", REPO_URL, f"+{BRANCH}:refs/remotes/origin/{BRANCH}")
        if not success:
            logger.error(f"Could not fetch updates from GitHub (is the repo still public?): {output}")
            return

        success, output = await run_git("rev-list", "--count", f"HEAD..origin/{BRANCH}")
        if not success:
            logger.error(f"Could not compare with GitHub: {output}")
            return

        if int(output) == 0:
            logger.info("Bot is up to date")
            return

        logger.info(f"{output} new commit(s) on GitHub. Updating...")

        if not await update_keeping_json_files():
            return

        await restart(bot)

    except Exception as e:
        logger.error(f"Error checking for updates: {e}")


async def update_keeping_json_files():
    """Fast-forward to the GitHub version while keeping the local JSON files.

    Returns:
        bool: True if the update was applied
    """
    _, tracked_output = await run_git("ls-files")
    tracked = set(tracked_output.splitlines())

    # Keep a copy of the JSON files and get them out of the way, so local changes don't block the update
    backups = {}
    for path in glob.glob(os.path.join(REPO_DIR, "*.json")):
        name = os.path.basename(path)
        with open(path, "rb") as f:
            backups[path] = f.read()
        if name in tracked:
            await run_git("checkout", "--", name)
        else:
            os.remove(path)

    try:
        success, output = await run_git("merge", "--ff-only", f"origin/{BRANCH}")
        if not success:
            logger.error(f"Could not apply update (local code changes or commits are in the way): {output}")
            return False
        _, commit = await run_git("log", "-1", "--format=%h %s")
        logger.info(f"Updated to {commit}")
        return True

    finally:
        # Put the local JSON files back, whether or not the update worked
        for path, content in backups.items():
            with open(path, "wb") as f:
                f.write(content)


async def restart(bot):
    """Restart the bot process so the new code is loaded."""
    logger.info("Restarting bot to load the update...")
    await bot.close()
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable] + sys.argv)
