"""One isolated plugin invocation. Never imported by the panel or CLI."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import sys
import traceback


def _offline(event, args):
    # Trusted plugin code only. Deny accidental network and process escape paths,
    # including urllib, UDP and external curl, before importing installed code.
    if (event in {'socket.connect', 'socket.sendto', 'socket.bind', 'socket.getaddrinfo',
                  'socket.gethostbyname', 'socket.gethostbyaddr', 'subprocess.Popen',
                  'os.system', 'os.posix_spawn', 'os.fork', 'os.forkpty', 'os.exec', 'os.spawn'}):
        raise PermissionError('network and external execution unavailable for this hook')


def _file_policy(plugin_path, data_path, temporary):
    plugin = Path(plugin_path).resolve().parent
    data = Path(data_path).resolve()
    temporary = Path(temporary).resolve()
    libraries = [Path(sys.base_prefix).resolve(), Path(sys.prefix).resolve()]

    def inside(path, roots):
        if isinstance(path, int):
            return True
        try:
            path = Path(os.fsdecode(path)).resolve()
            return any(path == root or root in path.parents for root in roots)
        except (TypeError, ValueError, OSError):
            return False

    def check(event, args):
        if event == 'open':
            mode = args[1] if isinstance(args[1], str) else ''
            flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            writing = any(c in mode for c in 'wax+') or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            roots = [data, temporary] if writing else [data, temporary, plugin, *libraries]
            if not inside(args[0], roots):
                raise PermissionError('file access outside plugin scope')
        elif event in ('os.listdir', 'os.scandir'):
            if not inside(args[0], [data, temporary, plugin, *libraries]):
                raise PermissionError('directory access outside plugin scope')
        elif event in ('os.remove', 'os.rmdir', 'os.mkdir', 'os.rename', 'os.replace', 'os.chmod', 'os.chown', 'os.link', 'os.symlink', 'os.truncate', 'os.utime', 'os.chflags', 'os.setxattr', 'os.removexattr'):
            paths = args[:2] if event in ('os.rename', 'os.replace', 'os.link', 'os.symlink') else args[:1]
            if any(not inside(path, [data, temporary]) for path in paths):
                raise PermissionError('file modification outside plugin scope')
    return check


def _resource_limits():
    if os.name != 'nt':
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CPU, (180, 181))
        resource.setrlimit(resource.RLIMIT_FSIZE, (128 * 1024 * 1024, 128 * 1024 * 1024))
        if sys.platform != 'darwin' and hasattr(resource, 'RLIMIT_AS'):
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))


def main():
    _resource_limits()
    path, hook, input_path, output_path = sys.argv[1:]
    with open(input_path, encoding='utf-8') as stream:
        request = json.load(stream)
    # Remove inherited credentials before importing any installed plugin code.
    keep = {key: value for key, value in os.environ.items() if key in ('SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'LANG', 'LC_ALL')}
    os.environ.clear()
    os.environ.update(keep)
    for key in ('TEMP', 'TMP', 'TMPDIR'):
        os.environ[key] = str(Path(output_path).resolve().parent)
    sys.addaudithook(_offline)
    sys.addaudithook(_file_policy(path, request['data_dir'], Path(output_path).parent))
    spec = importlib.util.spec_from_file_location('installed_plugin', path, submodule_search_locations=[os.path.dirname(path)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    result = getattr(module, hook)(request)
    encoded = json.dumps(result, ensure_ascii=False).encode('utf-8')
    if len(encoded) > 8 * 1024 * 1024:
        raise ValueError('result too large')
    with open(output_path, 'wb') as stream:
        stream.write(encoded)


if __name__ == '__main__':
    try:
        main()
    except BaseException as exc:
        # Preserve useful error type and plugin file:line, never raw exception
        # messages, config, inputs, local variables or arbitrary stdout.
        frames = traceback.extract_tb(exc.__traceback__)
        location = next((f'{Path(f.filename).name}:{f.lineno}' for f in reversed(frames)
                         if f.filename != __file__), 'plugin_worker.py')
        try:
            Path(sys.argv[-1]).with_name('error.json').write_text(
                json.dumps({'error': type(exc).__name__, 'location': location}), encoding='utf-8')
        except Exception:
            pass
        raise SystemExit(1)
