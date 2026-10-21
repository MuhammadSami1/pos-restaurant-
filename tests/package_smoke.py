"""Launch the actual built exe, check first-run data, and close its own window."""
from pathlib import Path
from contextlib import closing
import ctypes
from ctypes import wintypes
import json
import os
import sqlite3
import subprocess
import tempfile
import time

import win32con
import win32gui
import win32process

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXE = ROOT / "dist" / "KhataUpdate" / "RestaurantBilling.exe"
if not DEFAULT_EXE.is_file():
    DEFAULT_EXE = ROOT / "dist" / "RestaurantBilling.exe"
EXE = Path(os.environ.get("RESTAURANT_BILLING_TEST_EXE", DEFAULT_EXE)).resolve()
assert EXE.is_file(), "Build RestaurantBilling.exe first."


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
    ]


_kernel = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
_kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_kernel.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry))
_kernel.Process32FirstW.restype = wintypes.BOOL
_kernel.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry))
_kernel.Process32NextW.restype = wintypes.BOOL
_kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_kernel.OpenProcess.restype = wintypes.HANDLE
_kernel.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
_kernel.GetProcessTimes.restype = wintypes.BOOL
_kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
_kernel.CloseHandle.restype = wintypes.BOOL


def _process_parents():
    """Take an OS process snapshot; executable names confer no ownership."""
    snapshot = _kernel.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        parents = {}
        success = _kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while success:
            parents[entry.th32ProcessID] = entry.th32ParentProcessID
            success = _kernel.Process32NextW(snapshot, ctypes.byref(entry))
        if ctypes.get_last_error() not in (0, 18):  # ERROR_NO_MORE_FILES
            raise ctypes.WinError(ctypes.get_last_error())
        return parents
    finally:
        _kernel.CloseHandle(snapshot)


def _created_at(pid):
    """Include creation time in process identity so recycled PIDs cannot match."""
    handle = _kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not _kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                       ctypes.byref(kernel), ctypes.byref(user)):
            return None
        return (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        _kernel.CloseHandle(handle)


class OwnedProcessTree:
    """Track only this Popen launch and its verified PyInstaller descendants."""

    def __init__(self, process):
        created = _created_at(process.pid)
        if created is None:
            raise AssertionError("Could not identify the launched test process safely.")
        self.known = {process.pid: created}

    def current(self):
        parents = _process_parents()
        owned = {pid: created for pid, created in self.known.items()
                 if pid in parents and _created_at(pid) == created}
        while True:
            added = {}
            for pid, parent in parents.items():
                if pid in owned or parent not in owned:
                    continue
                created = _created_at(pid)
                # An older orphan with a reused parent PID is not our child.
                if created is not None and created >= owned[parent]:
                    added[pid] = created
            if not added:
                break
            owned.update(added)
        self.known.update(owned)
        return owned


def owned_windows(tree):
    owned = tree.current()
    results = []
    def visit(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid in owned and _created_at(pid) == owned[pid]:
            results.append((hwnd, win32gui.GetWindowText(hwnd)))
    win32gui.EnumWindows(visit, None)
    return results


def close_owned_windows(tree, title=None):
    for hwnd, caption in owned_windows(tree):
        if title is not None and caption != title:
            continue
        # Recheck the live window and process immediately before sending WM_CLOSE.
        if not win32gui.IsWindow(hwnd):
            continue
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        owned = tree.current()
        if pid in owned and _created_at(pid) == owned[pid]:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)


with tempfile.TemporaryDirectory(prefix="package-", dir=ROOT / ".test-data") as folder:
    env = dict(os.environ, RESTAURANT_BILLING_DATA_DIR=folder)
    started = time.perf_counter()
    process = subprocess.Popen([str(EXE)], cwd=ROOT, env=env)
    tree = None
    try:
        tree = OwnedProcessTree(process)
        windows = []
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            windows = owned_windows(tree)
            if any(title == "Restaurant Billing" for _, title in windows):
                break
            if process.poll() is not None:
                raise AssertionError(f"Executable exited early with code {process.returncode}")
            time.sleep(0.1)
        else:
            raise AssertionError(f"Billing window did not appear: {windows}")
        print(f"PACKAGE_LAUNCH_SECONDS={time.perf_counter() - started:.3f}", flush=True)
        # Allow the first frame to be painted and all callbacks to run.
        time.sleep(0.8)
        with closing(sqlite3.connect(Path(folder) / "restaurant.db")) as conn:
            assert conn.execute("SELECT COUNT(*) FROM dishes").fetchone()[0] == 9
            assert conn.execute("SELECT COUNT(*) FROM bills").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 0
            assert "customer_id" in {row[1] for row in conn.execute("PRAGMA table_info(bills)")}
        config = json.loads((Path(folder) / "config.json").read_text(encoding="utf-8"))
        assert config["theme"] == "dark"
        close_owned_windows(tree, title="Restaurant Billing")
        assert process.wait(timeout=10) == 0
        print("PACKAGE_SMOKE_OK: standalone billing window, seeded database, persisted config, clean exit", flush=True)
    finally:
        if process.poll() is None:
            if tree is not None:
                close_owned_windows(tree)
            else:
                # Identification failure: only the original Popen handle is trusted.
                process.terminate()
            process.wait(timeout=10)
