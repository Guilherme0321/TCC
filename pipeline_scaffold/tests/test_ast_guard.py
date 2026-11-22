"""Testes da análise estática de segurança (AD-05)."""

from infra.ast_guard import AstGuard, analyze


def test_allows_benign_tests():
    code = """
import pytest
from unittest.mock import MagicMock
from mymod import soma

def test_soma():
    assert soma(1, 2) == 3

def test_writes_to_tmp():
    with open('/tmp/out.txt', 'w') as f:
        f.write('ok')
"""
    result = analyze(code)
    assert result.allowed
    assert result.violations == []


def test_rejects_os_system():
    result = analyze("import os\ndef test_x():\n    os.system('rm -rf /')\n")
    assert not result.allowed
    assert any("os.system" in v for v in result.violations)


def test_rejects_subprocess_import():
    result = analyze("import subprocess\ndef test_x():\n    pass\n")
    assert not result.allowed
    assert any("subprocess" in v for v in result.violations)


def test_rejects_from_subprocess_import():
    result = analyze("from subprocess import run\n")
    assert not result.allowed


def test_rejects_shutil_rmtree():
    result = analyze("import shutil\nshutil.rmtree('/data')\n")
    assert not result.allowed
    assert any("shutil.rmtree" in v for v in result.violations)


def test_rejects_socket():
    result = analyze("import socket\n")
    assert not result.allowed


def test_rejects_eval_exec():
    assert not analyze("eval('1+1')").allowed
    assert not analyze("exec('x=1')").allowed


def test_rejects_dynamic_import_of_banned():
    result = analyze("__import__('socket')")
    assert not result.allowed


def test_rejects_from_os_import_system():
    result = analyze("from os import system\nsystem('ls')")
    assert not result.allowed


def test_rejects_write_outside_tempdir():
    result = analyze("open('/etc/passwd', 'w')")
    assert not result.allowed
    assert any("fora do tempdir" in v for v in result.violations)


def test_relative_write_is_allowed():
    # Escrita relativa fica no workdir efêmero — permitida.
    assert analyze("open('data.txt', 'w')").allowed


def test_syntax_error_is_not_security_rejection():
    result = analyze("def test(:\n    pass")
    assert result.allowed  # não é rejeição de segurança
    assert result.syntax_error is not None


def test_guard_counts_rejections():
    guard = AstGuard()
    guard.check("import os\nos.system('x')")  # rejeitado
    guard.check("def test_ok(): assert True")  # ok
    guard.check("import socket")  # rejeitado
    assert guard.checked == 3
    assert guard.rejections == 2
