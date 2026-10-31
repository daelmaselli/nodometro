import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, HTTPServer
from queue import Queue

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'collectors'))
sys.path.insert(0, str(ROOT / 'image-check'))
import run as runner
import prune
import collect


def varint(data, offset=0):
    value = shift = 0
    while True:
        byte = data[offset]; offset += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
        shift += 7


def unsnappy(data):
    size, offset = varint(data)
    result = bytearray()
    while offset < len(data):
        tag = data[offset]; offset += 1
        kind, length = tag & 3, tag >> 2
        if kind == 0:
            if length >= 60:
                count = length - 59
                length = int.from_bytes(data[offset:offset+count], 'little')
                offset += count
            length += 1
            result.extend(data[offset:offset+length]); offset += length
        else:
            if kind == 1:
                length = 4 + ((tag >> 2) & 7)
                distance = ((tag & 224) << 3) | data[offset]; offset += 1
            else:
                count = 2 if kind == 2 else 4
                distance = int.from_bytes(data[offset:offset+count], 'little'); offset += count
                length += 1
            for _ in range(length):
                result.append(result[-distance])
    assert len(result) == size
    return bytes(result)


def protobuf(data):
    offset = 0
    while offset < len(data):
        tag, offset = varint(data, offset)
        wire, number = tag & 7, tag >> 3
        if wire == 0:
            value, offset = varint(data, offset)
        elif wire == 2:
            length, offset = varint(data, offset)
            value = data[offset:offset+length]; offset += length
        elif wire in (1, 5):
            length = 8 if wire == 1 else 4
            value = data[offset:offset+length]; offset += length
        else:
            raise AssertionError('unexpected protobuf field')
        yield number, value


def samples(body):
    result = {}
    for number, series in protobuf(unsnappy(body)):
        if number != 1:
            continue
        labels, values = {}, []
        for field, item in protobuf(series):
            if field == 1:
                label = dict(protobuf(item))
                labels[label[1].decode()] = label[2].decode()
            elif field == 2:
                sample = dict(protobuf(item))
                values.append((struct.unpack('<d', sample.get(1, b'\0' * 8))[0], sample.get(2, 0)))
        result[tuple(sorted(labels.items()))] = values
    return result


class CollectorTests(unittest.TestCase):
    def test_failure_does_not_turn_previous_prune_data_into_success(self):
        result = prune.fields({'collection_success': 0, 'last_success_timestamp_seconds': 123,
                               'disk_usage': {'Images': [{'Size': 500}]}})
        self.assertEqual(result['reclaimable_total_bytes'], 0)
        self.assertEqual(result['last_success_timestamp_seconds'], 123)

    def test_legacy_docker_shared_layers_and_active_volumes(self):
        result = prune.reclaimable({'LayersSize': 99999,
            'Images': [{'Size': 100, 'SharedSize': 40, 'Containers': 0},
                       {'Size': 500, 'SharedSize': 0, 'Containers': 1}],
            'Containers': [{'State': 'exited', 'SizeRw': 10}, {'State': 'running', 'SizeRw': 30}],
            'Volumes': [{'UsageData': {'Size': 50, 'RefCount': 0}},
                        {'UsageData': {'Size': 20, 'RefCount': 1}}],
            'BuildCache': [{'Size': 12, 'InUse': False, 'Shared': False}]})
        self.assertEqual(result, [82, 50])

    def test_snapshot_replace_keeps_last_good_file_on_encoding_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'metrics.prom'
            runner.publish(path, 'old\n')
            with self.assertRaises(ValueError):
                runner.publish(path, runner.prometheus([('bad-name', {}, 1)], 10))
            self.assertEqual(path.read_text(), 'old\n')
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_numeric_parser_rejects_unsupported_string_field(self):
        with self.assertRaises(ValueError):
            list(runner.influx_rows('metric field="string"'))
        # The legacy Influx parser rejects a tag ending in a backslash, even
        # doubled by the emitter. Do not turn invalid old output into new series.
        with self.assertRaises(ValueError):
            list(runner.influx_rows(r'metric,tag=trailing\\ value=1i'))

    def test_tool_timeout_kills_process_group(self):
        with self.assertRaises(TimeoutError):
            runner.execute(['/bin/bash', '-c', 'sleep 20 & wait'], 0.1)


@unittest.skipUnless(shutil.which('telegraf'), 'run in the Telegraf test container for wire-format comparison')
class RemoteWriteCompatibility(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.queue = Queue()
        queue = self.queue
        class Receiver(BaseHTTPRequestHandler):
            def do_POST(self):
                queue.put(self.rfile.read(int(self.headers['Content-Length'])))
                self.send_response(204); self.end_headers()
            def log_message(self, *_):
                pass
        self.server = HTTPServer(('127.0.0.1', 0), Receiver)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def send(self, text, data_format, extra='', agent=None):
        fixture = self.directory / 'metrics'
        fixture.write_text(text)
        config = self.directory / 'test.conf'
        agent = agent or '[agent]\n  hostname = "compatibility-host"\n  interval = "30s"\n'
        config.write_text(f'''
{agent}
[[inputs.file]]
  files = ["{fixture}"]
  data_format = "{data_format}"
{extra}
[[outputs.http]]
  url = "http://127.0.0.1:{self.server.server_port}/api/v1/write"
  method = "POST"
  data_format = "prometheusremotewrite"
  content_encoding = "identity"
  [outputs.http.headers]
    Content-Type = "application/x-protobuf"
    Content-Encoding = "snappy"
    X-Prometheus-Remote-Write-Version = "0.1.0"
''')
        process = subprocess.run(['telegraf', '--once', '--config', str(config)],
                                 capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)
        return samples(self.queue.get(timeout=2))

    def compare(self, baseline, prom):
        self.assertEqual(set(baseline), set(prom))
        for key in baseline:
            self.assertEqual([value for value, _ in baseline[key]], [value for value, _ in prom[key]])
            self.assertEqual({timestamp for _, timestamp in prom[key]}, {1720000000000})

    def test_image_and_zfs_exact_names_labels_values_and_timestamps(self):
        report = {'success': 1, 'last_success': 1720000000, 'checks': [
            {'project': 'project', 'service': 'app', 'image': 'example:1', 'check_id': '0',
             'status': 'CHECK', 'reason': 'escaped space, equals= quote" slash\\ ü', 'registry_cached': False}]}
        reasons = [report['checks'][0]['reason'], r'path\safe', r'path\with\comma,equals= and space',
                   'new\nline and tab\t sanitized by the original emitter']
        report['checks'] = [dict(report['checks'][0], reason=reason, check_id=str(index))
                            for index, reason in enumerate(reasons)]
        with contextlib.redirect_stdout(io.StringIO()) as output:
            collect.emit(report, 21600)
        text = output.getvalue() + '''zfs_pool_status,pool=tank,health=ONLINE health_code=0i,size_bytes=1000000i,allocated_bytes=600000i,free_bytes=400000i,capacity_percent=60,fragmentation_percent=10
zfs_vdev,pool=tank,vdev=/dev/disk/by-id/example,state=ONLINE read_errors=0i,write_errors=1i,checksum_errors=2i
zfs_pool_errors,pool=tank read_errors=0i,write_errors=1i,checksum_errors=2i
zfs_dataset_space,pool=tank,dataset=tank/data used_bytes=100i,available_bytes=200i,snapshot_used_bytes=20i,dataset_used_bytes=80i,children_used_bytes=0i,logical_used_bytes=120i,compression_ratio=1.13
'''
        prom = runner.prometheus(runner.influx_rows(text), 1720000000000)
        baseline = self.send(text, 'influx')
        actual = self.send(prom, 'prometheus', '  prometheus_metric_version = 2\n')
        self.compare(baseline, actual)
        # Re-reading a stopped collector's snapshot must keep the old timestamp.
        self.assertEqual(actual, self.send(prom, 'prometheus', '  prometheus_metric_version = 2\n'))

    def test_agent_intervals_expand_defaults_and_overrides_in_real_remote_write(self):
        for filename in ('telegraf.conf', 'collectors/docker.conf'):
            agent = (ROOT / filename).read_text().split('[[', 1)[0]
            for interval, flush in [
                (None, None),
                ('60s', '30s'),
                ('30s', '60s'),
            ]:
                with self.subTest(config=filename, interval=interval, flush=flush):
                    with patch.dict(os.environ):
                        for key, value in [('TELEGRAF_INTERVAL', interval), ('TELEGRAF_FLUSH_INTERVAL', flush)]:
                            os.environ.pop(key, None)
                            if value is not None:
                                os.environ[key] = value
                        result = self.send('interval_probe value=1i', 'influx', agent=agent)
                    key = next(iter(result))
                    self.assertEqual(dict(key)['__name__'], 'interval_probe_value')
                    self.assertEqual(result[key][0][0], 1)
            # Ensure neither setting silently falls back when an override is
            # present: invalid durations must prevent startup before any write.
            for key in ('TELEGRAF_INTERVAL', 'TELEGRAF_FLUSH_INTERVAL'):
                with self.subTest(config=filename, invalid=key):
                    with patch.dict(os.environ, {key: 'not-a-duration'}):
                        result = subprocess.run(['telegraf', '--once', '--config', str(self.directory / 'test.conf')],
                                                capture_output=True, text=True, timeout=20)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('not-a-duration', result.stderr)

    def test_system_only_startup_without_custom_snapshots(self):
        directory = self.directory / 'metrics' / 'seed'
        directory.mkdir(parents=True)
        (directory / 'empty.prom').write_bytes((ROOT / 'collectors/empty.prom').read_bytes())
        config = self.directory / 'empty.conf'
        config.write_text((ROOT / 'telegraf.conf').read_text().replace(
            '/metrics/', str(self.directory / 'metrics') + '/').replace(
            '${VICTORIAMETRICS_URL}', 'http://127.0.0.1:9'))
        result = subprocess.run(['telegraf', '--config', str(config), '--input-filter', 'file', '--test'],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('E!', result.stderr)
        self.assertEqual(result.stdout, '')

    def test_successful_oneshot_is_ok_but_failed_and_regular_exits_stay_critical(self):
        source = tomllib.loads((ROOT / 'collectors/docker.conf').read_text())['processors']['starlark'][0]['source']
        for lifecycle, state, status, expected in [
            ('oneshot', 'exited', 'Exited (0) 1 minute ago', 0),
            ('oneshot', 'exited', 'Exited (1) 1 minute ago', 2),
            ('oneshot', 'exited', 'Exited (10) 1 minute ago', 2),
            ('', 'exited', 'Exited (0) 1 minute ago', 2),
            ('oneshot', 'dead', 'Dead', 2),
            ('', 'running', 'Up 1 minute (healthy)', 0),
        ]:
            with self.subTest(lifecycle=lifecycle, status=status):
                text = f'docker_inventory,container_name=/init,container_state={state},monitoring_lifecycle={lifecycle or "regular"} status="{status}",created=1i\n'
                result = self.send(text, 'influx', "[[processors.starlark]]\n  source = '''" + source + "'''\n")
                key = next(key for key in result if dict(key)['__name__'] == 'docker_inventory_state_code')
                self.assertEqual(result[key][0][0], expected)
                self.assertEqual(dict(key)['container_state'], state)
                self.assertEqual(dict(key)['container_name'], 'init')
                self.assertNotIn('monitoring_lifecycle', dict(key))

    def test_prune_real_starlark_matches_python_for_success_failure_and_summary(self):
        legacy = {'LayersSize': 9999, 'Images': [{'Size': 100, 'SharedSize': 40, 'Containers': 0}],
                  'Containers': [{'State': 'exited', 'SizeRw': 10}],
                  'Volumes': [{'UsageData': {'Size': 50, 'RefCount': 0}}],
                  'BuildCache': [{'Size': 12, 'InUse': False, 'Shared': False}]}
        modern = {'ImageUsage': {'Reclaimable': 100}, 'ContainerUsage': {'Reclaimable': 20},
                  'VolumeUsage': {'Reclaimable': 50}, 'BuildCacheUsage': {'Reclaimable': 5}}
        for success, disk in [(1, legacy), (1, modern), (0, legacy)]:
            with self.subTest(success=success, summary='ImageUsage' in disk):
                raw = {'collection_success': success, 'disk_usage': disk,
                       'last_success_timestamp_seconds': 123, 'refresh_interval_seconds': 10800}
                baseline = self.send(json.dumps(raw), 'value', f'''
  data_type = "string"
  name_override = "docker_prune_raw"
  [inputs.file.tags]
    source = "docker-prune-monitoring"
[[processors.starlark]]
  namepass = ["docker_prune_raw"]
  script = "{ROOT / 'docker-prune/parse.star'}"
''')
                rows = [('docker_prune_' + key, {'source': 'docker-prune-monitoring'}, value)
                        for key, value in prune.fields(raw).items()]
                prom = self.send(runner.prometheus(rows, 1720000000000), 'prometheus',
                                 '  prometheus_metric_version = 2\n')
                self.compare(baseline, prom)


if __name__ == '__main__':
    unittest.main()
