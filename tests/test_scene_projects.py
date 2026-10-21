"""Scene persistence, validation and competing editor revisions."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import multiprocessing
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.models import scene_projects as scenes


def _process_save(root, identifier, payload, ready, start, results):
    """Separate processes must obey the same revision check as editor tabs."""
    scenes.SCENES = Path(root)
    ready.put(True)
    if not start.wait(10):
        results.put('timeout')
        return
    try:
        scenes.save_scene(identifier, payload, lambda kind, ref: True)
        results.put('saved')
    except scenes.SceneConflict:
        results.put('conflict')
    except Exception as error:
        results.put(type(error).__name__ + ': ' + str(error))


class SceneProjectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='scene-project-test-')
        self.root = Path(self.temporary.name)
        self.scene_root = self.root / 'scenes'
        self.patcher = patch.object(scenes, 'SCENES', self.scene_root)
        self.patcher.start()
        self.assets = {('ply', 'location-a.ply'), ('spz', 'a' * 32)}

    def tearDown(self):
        self.patcher.stop()
        self.temporary.cleanup()

    def resolve(self, kind, ref):
        if (kind, ref) not in self.assets:
            raise FileNotFoundError(ref)
        return True

    def payload(self):
        return {
            'name': 'รวมสถานที่ทดสอบ', 'units': 'meters',
            'objects': [
                {'id': 'place-a', 'name': 'Location A',
                 'asset': {'kind': 'ply', 'ref': 'location-a.ply'},
                 'position': [1, 2, -3], 'rotation': [0, .5, 0], 'scale': 2},
                {'id': 'place-b', 'name': 'Location B',
                 'asset': {'kind': 'spz', 'ref': 'a' * 32},
                 'position': [12, -1, 0], 'rotation': [.1, -.2, .3], 'visible': False},
            ],
            'colliders': [
                {'id': 'ground', 'kind': 'floor', 'position': [0, -.1, 0], 'size': [20, .2, 15]},
                {'id': 'boundary', 'kind': 'wall', 'position': [5, 1.5, 0],
                 'yaw': 1.5707963267948966, 'size': [10, 3, .25], 'enabled': False},
            ],
            'view': {'position': [16, 5, 8], 'target': [3, 0, 0]},
            'spawn': {'position': [2, 1.7, 1], 'yaw': .75},
            'player': {'height': 1.7, 'radius': .3, 'speed': 2.2},
        }

    def test_composition_reopens_with_transforms_colliders_and_unicode(self):
        payload = self.payload()
        original = deepcopy(payload)
        document = scenes.create_scene(payload, self.resolve)
        self.assertEqual(payload, original, 'validation must not mutate the submitted scene')
        self.assertEqual(document['revision'], 1)
        self.assertEqual(document['version'], 1)
        self.assertRegex(document['id'], r'^[a-f0-9]{32}$')
        self.assertEqual(scenes.read_scene(document['id']), document)
        self.assertEqual(scenes.list_scenes(), [document])
        self.assertEqual(document['objects'][1]['asset']['kind'], 'spz')
        self.assertEqual(document['objects'][1]['rotation'], [.1, -.2, .3])
        self.assertFalse(document['colliders'][1]['enabled'])
        self.assertEqual(document['spawn'], payload['spawn'])
        self.assertEqual(document['player'], payload['player'])
        self.assertIn('รวมสถานที่ทดสอบ', (self.scene_root / (document['id'] + '.json')).read_text(encoding='utf8'))

    def test_empty_scene_and_missing_optional_fields_have_usable_defaults(self):
        document = scenes.create_scene({}, self.resolve)
        self.assertEqual(document['units'], 'relative')
        self.assertEqual(document['objects'], [])
        self.assertEqual(document['colliders'], [])
        self.assertEqual(document['view'], {'position': [6, 4, 6], 'target': [0, 0, 0]})
        self.assertEqual(document['spawn']['position'][1], document['player']['height'])
        with_defaults = scenes.create_scene({'objects': [{'asset': {'kind': 'ply', 'ref': 'location-a.ply'}}],
                                             'colliders': [{'kind': 'floor'}, {'kind': 'wall'}]}, self.resolve)
        self.assertEqual(with_defaults['objects'][0]['scale'], 1)
        self.assertEqual(with_defaults['objects'][0]['rotation'], [0, 0, 0])
        self.assertEqual(with_defaults['colliders'][0]['size'], [10, .2, 10])
        self.assertEqual(len({item['id'] for item in with_defaults['objects'] + with_defaults['colliders']}), 3)

    def test_save_and_delete_require_current_revision_and_preserve_source_assets(self):
        created = scenes.create_scene(self.payload(), self.resolve)
        updated = deepcopy(created)
        updated['objects'][0]['position'][0] = 24
        updated['name'] = 'Aligned locations'
        updated['revision'] = True
        with self.assertRaises(ValueError):
            scenes.save_scene(created['id'], updated, self.resolve)
        updated['revision'] = created['revision']
        saved = scenes.save_scene(created['id'], updated, self.resolve)
        self.assertEqual(saved['revision'], 2)
        self.assertEqual(saved['created_at'], created['created_at'])
        self.assertEqual(saved['objects'][0]['position'][0], 24)
        with self.assertRaises(scenes.SceneConflict) as conflict:
            scenes.save_scene(created['id'], updated, self.resolve)
        self.assertEqual(conflict.exception.current_revision, 2)
        with self.assertRaises(scenes.SceneConflict):
            scenes.delete_scene(created['id'], 1)
        self.assertEqual(scenes.read_scene(created['id']), saved)
        self.assertEqual(scenes.delete_scene(created['id'], 2), saved)
        self.assertEqual(scenes.list_scenes(), [])
        self.assertEqual(len(self.assets), 2)
        with self.assertRaises(FileNotFoundError):
            scenes.read_scene(created['id'])

    def test_deleted_assets_do_not_prevent_opening_scene(self):
        document = scenes.create_scene(self.payload(), self.resolve)
        self.assets.remove(('ply', 'location-a.ply'))
        self.assertEqual(scenes.read_scene(document['id']), document)
        self.assertEqual(scenes.list_scenes(), [document])
        with self.assertRaisesRegex(ValueError, 'Asset does not exist'):
            scenes.save_scene(document['id'], document, self.resolve)
        document['objects'] = document['objects'][1:]
        saved = scenes.save_scene(document['id'], document, self.resolve)
        self.assertEqual(len(saved['objects']), 1)

    def test_asset_validation_runs_before_any_document_is_written(self):
        payload = self.payload()
        payload['objects'][1]['asset']['ref'] = 'missing-record'
        with self.assertRaises(ValueError):
            scenes.create_scene(payload, self.resolve)
        self.assertFalse(self.scene_root.exists())
        for rejected in (None, False):
            with self.subTest(resolver_result=rejected), self.assertRaises(ValueError):
                scenes.create_scene(self.payload(), lambda kind, ref: rejected)

    def test_numeric_and_type_validation_rejects_invalid_scene_data(self):
        invalid = [
            ('version', 2), ('version', True), ('units', 'feet'), ('units', []),
            ('name', ''), ('name', 'x' * 161), ('name', 'line\nbreak'),
            ('objects', {}), ('colliders', 'wall'), ('view', []), ('player', None),
        ]
        for key, value in invalid:
            with self.subTest(field=key, value=value), self.assertRaises(ValueError):
                scenes.create_scene({key: value}, self.resolve)
        object_invalid = [
            ('position', [0, 1]), ('position', [0, 0, float('nan')]),
            ('position', [True, 0, 0]), ('position', [0, 0, 1e9]),
            ('rotation', [0, float('inf'), 0]), ('scale', 0), ('scale', -1),
            ('scale', '1'), ('scale', float('nan')), ('scale', 10**1000),
            ('visible', 1), ('asset', []), ('asset', {'kind': [], 'ref': 'x'}),
        ]
        for key, value in object_invalid:
            payload = self.payload()
            payload['objects'][0][key] = value
            with self.subTest(model_field=key, value=value), self.assertRaises(ValueError):
                scenes.create_scene(payload, self.resolve)
        collider_invalid = [('size', [10, 0, 10]), ('yaw', float('inf')),
                            ('kind', 'mesh'), ('kind', []), ('enabled', 'true')]
        for key, value in collider_invalid:
            payload = self.payload()
            payload['colliders'][0][key] = value
            with self.subTest(collider_field=key), self.assertRaises(ValueError):
                scenes.create_scene(payload, self.resolve)
        for group, field, value in [('spawn', 'yaw', float('nan')), ('view', 'target', [0, 1]),
                                    ('player', 'height', 0), ('player', 'radius', -1),
                                    ('player', 'speed', 1e8)]:
            with self.subTest(group=group, field=field), self.assertRaises(ValueError):
                scenes.create_scene({group: {field: value}}, self.resolve)

    def test_item_counts_and_duplicate_ids_are_bounded(self):
        for group, count, item in [('objects', scenes.MAX_OBJECTS + 1,
                                    {'asset': {'kind': 'ply', 'ref': 'location-a.ply'}}),
                                   ('colliders', scenes.MAX_COLLIDERS + 1, {'kind': 'floor'})]:
            with self.subTest(group=group), self.assertRaises(ValueError):
                scenes.create_scene({group: [deepcopy(item) for _ in range(count)]}, self.resolve)
        payload = self.payload()
        payload['colliders'][0]['id'] = payload['objects'][0]['id']
        with self.assertRaisesRegex(ValueError, 'unique'):
            scenes.create_scene(payload, self.resolve)

    def test_scene_and_asset_paths_cannot_escape_the_store(self):
        for identifier in ['../other', 'a/../../b', 'C:\\outside', '..', '', None,
                           'a' * 31, 'a' * 32 + '.json', 'A' * 32]:
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                scenes.read_scene(identifier)
        for ref in ['../outside.ply', 'folder/model.ply', 'folder\\model.ply',
                    'C:outside.ply', '..', '.', 'bad\0.ply', 'https://example.com/a.spz']:
            resolver = Mock(return_value=True)
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                scenes.create_scene({'objects': [{'asset': {'kind': 'ply', 'ref': ref}}]}, resolver)
            resolver.assert_not_called()
        payload = self.payload()
        payload['objects'][0]['id'] = '../outside'
        with self.assertRaises(ValueError):
            scenes.create_scene(payload, self.resolve)
        document = scenes.create_scene(self.payload(), self.resolve)
        document['id'] = 'b' * 32
        with self.assertRaises(ValueError):
            scenes.save_scene('a' * 32, document, self.resolve)

    def test_listing_ignores_incomplete_or_unrelated_files(self):
        document = scenes.create_scene({}, self.resolve)
        (self.scene_root / 'unrelated.json').write_text('{}')
        (self.scene_root / ('b' * 32 + '.json')).write_text('{broken')
        (self.scene_root / ('c' * 32 + '.json')).write_text(json.dumps({'id': 'd' * 32}))
        (self.scene_root / '.scene-interrupted.tmp').write_text('unfinished')
        self.assertEqual(scenes.list_scenes(), [document])

    def test_failed_atomic_replace_preserves_previous_scene(self):
        document = scenes.create_scene(self.payload(), self.resolve)
        original = scenes.read_scene(document['id'])
        document['name'] = 'This write must fail'
        with patch.object(scenes.os, 'replace', side_effect=OSError('disk write failed')):
            with self.assertRaises(OSError):
                scenes.save_scene(document['id'], document, self.resolve)
        self.assertEqual(scenes.read_scene(document['id']), original)
        self.assertEqual(list(self.scene_root.glob('*.tmp')), [])

    def test_competing_editor_tabs_cannot_lose_updates(self):
        document = scenes.create_scene(self.payload(), self.resolve)
        ready = threading.Barrier(8)

        def update(index):
            payload = deepcopy(document)
            payload['name'] = f'Editor {index}'
            ready.wait(timeout=10)
            try:
                return ('saved', scenes.save_scene(document['id'], payload, self.resolve))
            except scenes.SceneConflict as error:
                return ('conflict', error.current_revision)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(update, range(8)))
        winners = [value for status, value in results if status == 'saved']
        self.assertEqual(len(winners), 1)
        self.assertEqual([value for status, value in results if status == 'conflict'], [2] * 7)
        self.assertEqual(scenes.read_scene(document['id']), winners[0])

    def test_separate_backend_processes_cannot_lose_updates(self):
        document = scenes.create_scene(self.payload(), self.resolve)
        context = multiprocessing.get_context('spawn')
        ready, results = context.Queue(), context.Queue()
        start = context.Event()
        processes = [context.Process(target=_process_save, args=(str(self.scene_root), document['id'],
                                                                 document, ready, start, results)) for _ in range(2)]
        try:
            for process in processes:
                process.start()
            for _ in processes:
                self.assertTrue(ready.get(timeout=15))
            start.set()
            outcomes = [results.get(timeout=15) for _ in processes]
            self.assertCountEqual(outcomes, ['saved', 'conflict'])
            for process in processes:
                process.join(timeout=10)
                self.assertEqual(process.exitcode, 0)
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=5)
            ready.close()
            results.close()
        self.assertEqual(scenes.read_scene(document['id'])['revision'], 2)


if __name__ == '__main__':
    unittest.main()
