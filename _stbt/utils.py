from __future__ import unicode_literals
from __future__ import print_function
from __future__ import division
from __future__ import absolute_import
from future import standard_library
standard_library.install_aliases()
from builtins import *
import errno
import os
import tempfile
from contextlib import contextmanager
from shutil import rmtree


def mkdir_p(d):
    """Python 3.2 has an optional argument to os.makedirs called exist_ok.  To
    support older versions of python we can't use this and need to catch
    exceptions"""
    try:
        os.makedirs(d)
    except OSError as e:
        if e.errno == errno.EEXIST and os.path.isdir(d) \
                and os.access(d, os.R_OK | os.W_OK):
            return
        else:
            raise


def rm_f(filename):
    """Like ``rm -f``, it ignores errors if the file doesn't exist."""
    try:
        os.remove(filename)
    except OSError as e:
        if e.errno != errno.ENOENT:
            raise


@contextmanager
def named_temporary_directory(
        suffix='', prefix='tmp', dir=None):  # pylint:disable=redefined-builtin
    dirname = tempfile.mkdtemp(suffix, prefix, dir)
    try:
        yield dirname
    finally:
        rmtree(dirname)


@contextmanager
def scoped_curdir():
    with named_temporary_directory() as tmpdir:
        olddir = os.path.abspath(os.curdir)
        os.chdir(tmpdir)
        try:
            yield olddir
        finally:
            os.chdir(olddir)


def find_import_name(filename):
    """
    To import an arbitrary filename we need to set PYTHONPATH and we need to
    know the name of the module we're importing.  This is complicated by Python
    packages: for a directory structure like this:

        tests/package/a.py
        tests/package/__init__.py

    we want to add `tests` to `PYTHONPATH` (`sys.path`) and `import package.a`.
    This function traverses the directories to work out what `PYTHONPATH` and
    the module name should be returning them as a tuple.
    """
    import_dir, module_file = os.path.split(os.path.abspath(filename))
    import_name, module_ext = os.path.splitext(module_file)
    if module_ext != '.py':
        raise ImportError("Invalid module filename '%s'" % filename)

    while os.path.exists(os.path.join(import_dir, "__init__.py")):
        import_dir, s = os.path.split(import_dir)
        import_name = "%s.%s" % (s, import_name)
    return import_dir, import_name
