"""Opt-in Docker integration check; only temporary containers/volumes are used.

Build the Compose collector images first, then run from the repository:
    python3 tests/check_named_volumes.py
No production containers, sockets or metric endpoints are mounted.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
BIND_ROOT = Path(os.environ.get('NODOMETRO_BIND_ROOT', str(ROOT)))


def docker(*args):
    return subprocess.run(['docker', *args], cwd=ROOT, check=True,
                          capture_output=True, text=True, timeout=90).stdout


def main():
    spec = json.loads(docker('compose', '--env-file', '.env.example', '-f',
                            'docker-compose.yml', '--profile', '*', 'config', '--format', 'json'))
    prefix = 'nodometro-volume-test-' + uuid.uuid4().hex[:12]
    created = set()
    common = ['run', '--rm', '--pull', 'never', '--network', 'none', '--read-only',
              '--user', '65534:65534', '--cap-drop', 'ALL',
              '--security-opt', 'no-new-privileges:true']

    def volumes(service):
        args = []
        for mount in spec['services'][service]['volumes']:
            if mount['type'] != 'volume':
                continue
            name = prefix + '-' + mount['source']
            created.add(name)
            text = f'type=volume,src={name},dst={mount["target"]}'
            if mount.get('read_only'):
                text += ',readonly'
            if mount.get('volume', {}).get('nocopy'):
                text += ',volume-nocopy'
            args += ['--mount', text]
        return args

    def reader(config):
        mounts = volumes('telegraf') + [
            '--mount', f'type=bind,src={BIND_ROOT}/collectors/empty.prom,dst=/metrics/seed/empty.prom,readonly',
            '--mount', f'type=bind,src={config},dst=/check.conf,readonly']
        return docker(*common, *mounts, '--entrypoint', 'telegraf',
                      spec['services']['telegraf']['image'], '--test', '--config', '/check.conf')

    try:
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests/.tmp') as temp:
            config = Path(temp) / 'check.conf'
            # Same parser and glob as production, without host inputs or output.
            source = (ROOT / 'telegraf.conf').read_text()
            config.write_text('[agent]\n  omit_hostname = true\n' +
                              '[[inputs.file]]' + source.split('[[inputs.file]]', 1)[1])
            config.chmod(0o644)
            config.parent.chmod(0o755)
            bind_config = BIND_ROOT / config.relative_to(ROOT)
            assert reader(bind_config) == '', 'System-only startup must accept empty volumes'
            for collector in ('image-check', 'docker-prune', 'zfs'):
                probe = '''
import os, pathlib, sys
sys.path.insert(0, '/app/collectors')
from run import publish
assert os.getuid() == 65534
for directory in ('/metrics', CACHE):
    if directory:
        path = pathlib.Path(directory)
        assert path.stat().st_uid == 65534, (path, path.stat())
        assert path.stat().st_mode & 0o777 == (0o755 if directory == '/metrics' else 0o750)
        (path / '.probe').write_text('cache')
publish(pathlib.Path('/metrics/probe.prom'), 'volume_probe{collector="COLLECTOR"} 1 1720000000000\\n')
assert not pathlib.Path('/app/image-check/settings.json').exists(), 'Local settings leaked into image'
'''.replace('CACHE', repr({'image-check': '/var/cache/compose-image-check',
                          'docker-prune': '/var/cache/docker-prune'}.get(collector, ''))).replace('COLLECTOR', collector)
                docker(*common, *volumes(collector), '--entrypoint', 'python3',
                       spec['services'][collector]['image'], '-c', probe)
            output = reader(bind_config)
            for collector in ('image-check', 'docker-prune', 'zfs'):
                assert f'collector={collector}' in output, output
            # Repeat startup on populated volumes: snapshots must survive.
            assert output == reader(bind_config), 'Re-reading changed sample timestamps'
            mounts = volumes('telegraf')
            docker(*common, *mounts, '--entrypoint', '/bin/sh',
                   spec['services']['telegraf']['image'], '-ec',
                   'for dir in /metrics/images /metrics/prune /metrics/zfs; do '
                   'if touch "$dir/forbidden" 2>/dev/null; then exit 1; fi; done')
            # The configured file mount must expose only settings, never sources.
            settings = next(m for m in spec['services']['image-check']['volumes']
                            if m['target'] == '/app/image-check/settings.json')
            docker(*common, '--mount', f'type=bind,src={BIND_ROOT}/image-check/settings.json.example,dst={settings["target"]},readonly',
                   '--entrypoint', 'python3', spec['services']['image-check']['image'], '-c',
                   'import json; assert json.load(open("/app/image-check/settings.json"))["compose_root"] == "/compose-host"')
            print('PASS: reader first, three non-root writers, cache permissions, stable snapshots, read-only reader, settings mount')
    finally:
        for name in sorted(created):
            docker('volume', 'rm', name)


if __name__ == '__main__':
    main()
