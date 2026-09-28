"""Tests for packages.sh — the package-manager lookup install.sh depends on.

WHY these run with a faked PATH instead of in a container: the only input the
lookup has is "which package-manager binary exists", so a directory holding a
few empty executable files reproduces a Fedora, an Arch or an openSUSE machine
exactly, on any computer, in milliseconds. Every branch is reachable that way,
which means `make test` on a Mac covers the distributions nobody here runs.

PATH is replaced outright rather than prepended to, so a real apt on the machine
running the tests cannot leak into a "this is Arch" case. The only external
command the lookup needs is `sort`, so that one is linked into the fake bin.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL_ROOT = os.path.dirname(os.path.dirname(HERE))
PACKAGES_SH = os.path.join(TOOL_ROOT, "packages.sh")
BASH = shutil.which("bash") or "/bin/bash"

# Every package manager the lookup knows, plus the abstract requirements
# install.sh asks about. Kept here so a new one cannot be added to packages.sh
# without a test naming it.
MANAGERS = ("apt", "dnf", "pacman", "zypper")
NEEDS = ("python", "venv", "compiler", "kernel_headers", "python_headers", "wmctrl")


class FakeMachine:
    """A PATH containing exactly the commands you name, and nothing else."""

    def __init__(self, commands):
        self.dir = tempfile.mkdtemp(prefix="ee-fake-bin-")
        # `sort` is the one external command packages.sh uses; without it the
        # dedup step would fail for reasons that have nothing to do with the
        # branch under test.
        real_sort = shutil.which("sort")
        assert real_sort, "the test host has no sort(1)"
        os.symlink(real_sort, os.path.join(self.dir, "sort"))
        for command in commands:
            path = os.path.join(self.dir, command)
            with open(path, "w") as handle:
                handle.write("#!/bin/sh\nexit 0\n")
            os.chmod(path, 0o755)

    def run(self, snippet):
        """Source packages.sh on this fake machine and run one line of bash.

        Returns (stdout stripped of its trailing newline, exit status).
        """
        script = 'set -u\n. "$1"\n' + snippet + "\n"
        # WHY the absolute path: PATH below is replaced outright, so a bare
        # "bash" could not be found to launch in the first place.
        result = subprocess.run(
            [BASH, "-c", script, "bash", PACKAGES_SH],
            capture_output=True,
            text=True,
            env={"PATH": self.dir},
        )
        return result.stdout.rstrip("\n"), result.returncode

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def on(commands):
    return FakeMachine(commands)


class DetectionTest(unittest.TestCase):
    """pkg_manager, over every combination that changes its answer."""

    def assert_detects(self, commands, expected):
        machine = on(commands)
        self.addCleanup(machine.cleanup)
        out, status = machine.run("pkg_manager")
        self.assertEqual(status, 0)
        self.assertEqual(out, expected, f"with {commands!r} on PATH")

    def test_apt(self):
        self.assert_detects(["apt"], "apt")

    def test_apt_get_only(self):
        # Debian 12 and older Ubuntu images ship apt-get; some minimal images
        # have it without the newer `apt` wrapper.
        self.assert_detects(["apt-get"], "apt")

    def test_dnf(self):
        self.assert_detects(["dnf"], "dnf")

    def test_pacman(self):
        self.assert_detects(["pacman"], "pacman")

    def test_zypper(self):
        self.assert_detects(["zypper"], "zypper")

    def test_nothing_recognised(self):
        self.assert_detects([], "unknown")

    def test_unrelated_commands_do_not_count(self):
        self.assert_detects(["python3", "gcc", "make"], "unknown")

    def test_apt_wins_when_several_are_present(self):
        # A machine with both (an apt distro where someone installed dnf) must
        # still take the Debian path, whose wording is the proven one.
        self.assert_detects(["apt", "dnf", "pacman", "zypper"], "apt")

    def test_dnf_beats_pacman_and_zypper(self):
        self.assert_detects(["dnf", "pacman", "zypper"], "dnf")

    def test_pacman_beats_zypper(self):
        self.assert_detects(["pacman", "zypper"], "pacman")


class PackageNameTest(unittest.TestCase):
    """pkg_name — every manager crossed with every requirement."""

    def setUp(self):
        self.machine = on([])
        self.addCleanup(self.machine.cleanup)

    def name(self, manager, need):
        out, status = self.machine.run(f'pkg_name {manager} {need}')
        self.assertEqual(status, 0)
        return out

    def test_debian_names(self):
        self.assertEqual(self.name("apt", "python"), "python3")
        self.assertEqual(self.name("apt", "venv"), "python3-venv")
        self.assertEqual(self.name("apt", "compiler"), "build-essential")
        self.assertEqual(self.name("apt", "kernel_headers"), "build-essential")
        self.assertEqual(self.name("apt", "python_headers"), "python3-dev")
        self.assertEqual(self.name("apt", "wmctrl"), "wmctrl")

    def test_fedora_names(self):
        self.assertEqual(self.name("dnf", "python"), "python3")
        self.assertEqual(self.name("dnf", "venv"), "python3")
        self.assertEqual(self.name("dnf", "compiler"), "gcc")
        self.assertEqual(self.name("dnf", "kernel_headers"), "kernel-headers")
        # The whole point of this change: python3-dev does not exist on Fedora.
        self.assertEqual(self.name("dnf", "python_headers"), "python3-devel")
        self.assertEqual(self.name("dnf", "wmctrl"), "wmctrl")

    def test_arch_names(self):
        self.assertEqual(self.name("pacman", "python"), "python")
        self.assertEqual(self.name("pacman", "venv"), "python")
        self.assertEqual(self.name("pacman", "compiler"), "base-devel")
        self.assertEqual(self.name("pacman", "kernel_headers"), "linux-api-headers")
        self.assertEqual(self.name("pacman", "python_headers"), "python")
        self.assertEqual(self.name("pacman", "wmctrl"), "wmctrl")

    def test_opensuse_names(self):
        self.assertEqual(self.name("zypper", "python"), "python3")
        self.assertEqual(self.name("zypper", "venv"), "python3")
        self.assertEqual(self.name("zypper", "compiler"), "gcc")
        self.assertEqual(self.name("zypper", "kernel_headers"), "linux-glibc-devel")
        self.assertEqual(self.name("zypper", "python_headers"), "python3-devel")
        self.assertEqual(self.name("zypper", "wmctrl"), "wmctrl")

    def test_every_manager_names_every_need(self):
        # A gap here would print a short command line that quietly leaves out
        # one of the things the install actually needs.
        for manager in MANAGERS:
            for need in NEEDS:
                with self.subTest(manager=manager, need=need):
                    self.assertNotEqual(self.name(manager, need), "")

    def test_unknown_manager_names_nothing(self):
        self.assertEqual(self.name("unknown", "compiler"), "")

    def test_unknown_need_names_nothing(self):
        self.assertEqual(self.name("apt", "something_else"), "")


class InstallCommandTest(unittest.TestCase):
    """pkg_install_command — the line the user is told to run."""

    def setUp(self):
        self.machine = on([])
        self.addCleanup(self.machine.cleanup)

    def command(self, manager, *needs):
        return self.machine.run(
            'pkg_install_command {} {}'.format(manager, " ".join(needs))
        )

    def test_debian_line_is_unchanged(self):
        # This exact string is what install.sh printed before this change, and
        # what the README and knowledge/lessons.md both quote. It must not move.
        out, status = self.command("apt", "venv", "compiler", "python_headers")
        self.assertEqual(status, 0)
        self.assertEqual(out, "sudo apt install build-essential python3-dev python3-venv")

    def test_fedora_line(self):
        out, status = self.command("dnf", "compiler", "python_headers")
        self.assertEqual(status, 0)
        self.assertEqual(out, "sudo dnf install gcc python3-devel")

    def test_arch_line_collapses_to_two_packages(self):
        # venv and the Python headers both live in Arch's single `python`.
        out, status = self.command("pacman", "venv", "compiler", "python_headers")
        self.assertEqual(status, 0)
        self.assertEqual(out, "sudo pacman -S base-devel python")

    def test_opensuse_line(self):
        out, status = self.command("zypper", "compiler", "python_headers")
        self.assertEqual(status, 0)
        self.assertEqual(out, "sudo zypper install gcc python3-devel")

    def test_duplicates_are_collapsed(self):
        # A missing compiler and a missing linux/input.h are the same Debian
        # package; the line must not say build-essential twice.
        out, status = self.command("apt", "compiler", "kernel_headers")
        self.assertEqual(status, 0)
        self.assertEqual(out, "sudo apt install build-essential")

    def test_order_of_needs_does_not_change_the_line(self):
        forwards, _ = self.command("apt", "venv", "compiler", "python_headers")
        backwards, _ = self.command("apt", "python_headers", "compiler", "venv")
        self.assertEqual(forwards, backwards)

    def test_single_need(self):
        out, status = self.command("apt", "wmctrl")
        self.assertEqual(status, 0)
        self.assertEqual(out, "sudo apt install wmctrl")

    def test_unknown_manager_prints_nothing_and_fails(self):
        out, status = self.command("unknown", "compiler", "python_headers")
        self.assertEqual(out, "")
        self.assertEqual(status, 1)

    def test_no_nameable_package_fails(self):
        # A need this manager has no name for must not produce "sudo apt install"
        # with an empty package list.
        out, status = self.command("apt", "something_else")
        self.assertEqual(out, "")
        self.assertEqual(status, 1)

    def test_no_needs_at_all_fails(self):
        out, status = self.machine.run("pkg_install_command apt")
        self.assertEqual(out, "")
        self.assertEqual(status, 1)


class GenericDescriptionTest(unittest.TestCase):
    """pkg_generic_description — what an unrecognised distribution is told."""

    def setUp(self):
        self.machine = on([])
        self.addCleanup(self.machine.cleanup)

    def describe(self, need):
        out, status = self.machine.run(f"pkg_generic_description {need}")
        self.assertEqual(status, 0)
        return out

    def test_every_need_has_plain_words(self):
        for need in NEEDS:
            with self.subTest(need=need):
                self.assertNotEqual(self.describe(need), "")

    def test_descriptions_name_no_package(self):
        # The point of these strings is that they are NOT package names — a
        # wrong package name is what this whole change exists to stop printing.
        self.assertIn("C compiler", self.describe("compiler"))
        self.assertIn("Python.h", self.describe("python_headers"))
        self.assertIn("ensurepip", self.describe("venv"))
        self.assertIn("linux/input.h", self.describe("kernel_headers"))

    def test_unknown_need_describes_nothing(self):
        self.assertEqual(self.describe("something_else"), "")


class EndToEndOnFakeMachinesTest(unittest.TestCase):
    """Detection and naming together, as install.sh chains them."""

    def advise(self, commands, *needs):
        machine = on(commands)
        self.addCleanup(machine.cleanup)
        return machine.run(
            'pkg_install_command "$(pkg_manager)" {}'.format(" ".join(needs))
        )

    def test_a_fedora_machine_is_told_dnf(self):
        out, status = self.advise(["dnf"], "venv", "compiler", "python_headers")
        self.assertEqual(status, 0)
        self.assertEqual(out, "sudo dnf install gcc python3 python3-devel")

    def test_an_arch_machine_is_told_pacman(self):
        out, status = self.advise(["pacman"], "venv", "compiler", "python_headers")
        self.assertEqual(status, 0)
        self.assertEqual(out, "sudo pacman -S base-devel python")

    def test_an_opensuse_machine_is_told_zypper(self):
        out, status = self.advise(["zypper"], "venv", "compiler", "python_headers")
        self.assertEqual(status, 0)
        self.assertEqual(out, "sudo zypper install gcc python3 python3-devel")

    def test_an_ubuntu_machine_is_told_apt(self):
        out, status = self.advise(["apt"], "venv", "compiler", "python_headers")
        self.assertEqual(status, 0)
        self.assertEqual(out, "sudo apt install build-essential python3-dev python3-venv")

    def test_an_unrecognised_machine_is_told_nothing(self):
        out, status = self.advise([], "venv", "compiler", "python_headers")
        self.assertEqual(out, "")
        self.assertEqual(status, 1)


if __name__ == "__main__":
    unittest.main()
