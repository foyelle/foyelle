# Disk encryption and the recovery console: the root on LUKS unlocks at boot, the apply helper reports whether the public
# initial passphrase still works, and an approved request opens the passphrase prompt on tty8,
# where the new passphrase is typed on the keyboard (never through Foyelle), then used at boot.
import json
import time

INITIAL = "foyelle"
NEW = "correct horse battery staple"
RECOVERY = "tiny paper lantern 42"


def helper(req):
    return json.loads(machine.succeed(f"python3 {helper_client} '{json.dumps(req)}'"))


def approve(tool):
    token = "test" + str(time.time_ns())
    body = json.dumps({"tool": tool, "expires": time.time() + 600})
    machine.succeed(f"mkdir -p /run/foyelle/approvals && echo '{body}' > /run/foyelle/approvals/{token}")
    return token


def on_tty8(expected, keys=None):
    """Wait for a prompt on tty8, then type into it; show the screen and the unit's log on failure."""
    try:
        machine.wait_until_tty_matches("8", expected, timeout=120)
    except Exception:
        print("tty8:", machine.get_tty_text("8"))
        print(machine.execute("systemctl status foyelle-disk-passphrase --no-pager; fgconsole; "
                              "journalctl -b -u foyelle-disk-passphrase --no-pager")[1])
        raise
    if keys is not None:
        machine.sleep(1)  # cryptsetup flushes input typed while it sets up the terminal
        machine.send_chars(keys)


def unlock(passphrase):
    machine.start()
    machine.wait_for_console_text("Please enter passphrase for disk")
    machine.send_console(passphrase + "\n")
    machine.wait_for_unit("multi-user.target")
    assert "/dev/mapper/root on / type ext4" in machine.succeed("mount")


with subtest("format the LUKS root with the initial passphrase and boot from it"):
    machine.wait_for_unit("multi-user.target")
    assert helper({"disk": "status"}) == {"ok": True, "encrypted": False}
    machine.succeed(f"echo -n {INITIAL} | cryptsetup luksFormat -q --iter-time=1 /dev/vdb -")
    machine.succeed(f"echo -n {INITIAL} | cryptsetup luksOpen -q /dev/vdb root")
    machine.succeed("mkfs.ext4 -q /dev/mapper/root && cryptsetup close root")
    machine.succeed(f"{luks_system}/bin/switch-to-configuration boot")
    machine.succeed("sync")
    machine.crash()
    unlock(INITIAL)

with subtest("the helper warns while the initial passphrase works"):
    machine.wait_for_unit("foyelle-apply.socket")
    status = helper({"disk": "status"})
    assert status["encrypted"] and status["initial_passphrase"] is True, status

with subtest("the prompt needs an approval token for this tool"):
    refused = helper({"disk": "change-passphrase", "approval_token": approve("system.apply")})
    assert refused.get("refused"), refused
    machine.fail("systemctl is-active foyelle-disk-passphrase.service")

with subtest("the passphrase is changed on tty8 from the keyboard"):
    reply = helper({"disk": "change-passphrase", "approval_token": approve("system.disk_passphrase")})
    assert reply["status"] == "prompt-open", reply
    on_tty8("Enter passphrase to be changed", INITIAL + "\n")
    on_tty8("Enter new passphrase", NEW + "\n")
    on_tty8("Verify passphrase", NEW + "\n")
    on_tty8("The disk passphrase was changed", "\n")
    machine.wait_until_fails("systemctl is-active foyelle-disk-passphrase.service")
    status = helper({"disk": "status"})
    assert status["initial_passphrase"] is False, status
    # Nothing the user typed reached Foyelle's logs.
    machine.fail(f"journalctl -b | grep -F '{NEW}'")
    machine.fail(f"grep -rF '{NEW}' /var/lib/foyelle")

with subtest("the new passphrase unlocks the disk at boot"):
    machine.succeed("sync")
    machine.crash()
    unlock(NEW)

with subtest("the recovery console password is set on tty8, then logs in on tty2"):
    machine.wait_for_unit("foyelle-apply.socket")
    status = helper({"recovery": "status"})
    assert status["enabled"] and status["password_set"] is False, status
    reply = helper({"recovery": "set-password", "approval_token": approve("system.recovery_password")})
    assert reply["status"] == "prompt-open", reply
    on_tty8("New password", RECOVERY + "\n")
    on_tty8("Retype new password", RECOVERY + "\n")
    on_tty8("The recovery password was set", "\n")
    machine.wait_until_fails("systemctl is-active foyelle-recovery-password.service")
    assert helper({"recovery": "status"})["password_set"] is True
    machine.fail(f"journalctl -b | grep -F '{RECOVERY}'")
    machine.send_key("ctrl-alt-f2")
    machine.wait_until_tty_matches("2", "login: ")
    machine.send_chars("recovery\n")
    machine.wait_until_tty_matches("2", "Password: ")
    machine.send_chars(RECOVERY + "\n")
    machine.wait_until_succeeds("pgrep -u recovery -t tty2")
