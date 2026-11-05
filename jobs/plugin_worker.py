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


def main():
    if os.name != 'nt':
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CPU, (180, 181))
        resource.setrlimit(resource.RLIMIT_FSIZE, (128 * 1024 * 1024, 128 * 1024 * 1024))
        if sys.platform != 'darwin' and hasattr(resource, 'RLIMIT_AS'):
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    path, hook, input_path, output_path = sys.argv[1:]
    with open(input_path, encoding='utf-8') as stream:
        request = json.load(stream)
    if not request.get('allow_network'):
        sys.addaudithook(_offline)
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
