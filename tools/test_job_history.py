"""Local posting history in "copy job", the agent API and its MCP-facing shape.

Handwritten synthetic records only; isolated temporary DB and loopback port.
"""
from contextlib import closing
from pathlib import Path
import http.client
import json
import sys
import tempfile
import threading
import unittest
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobs import agent_api as api, db, history, panel, panel_views

TOKEN = 'synthetic-history-token-not-a-real-secret-0000'
BATCH_START = '2026-11-05T20:30:00+00:00'
VERDICT_WORDS = ('genuine', 'scam', 'closed.', 'same position', '已下架', '已关闭', '真实', '诈骗', '同一职位')


class JobHistory(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.path = root/'synthetic.db'
        (root/'skills.yaml').write_text('skills: []\n')
        self.config = {'db_path': str(self.path), 'skills_path': str(root/'skills.yaml'), 'panel': {'page_size': 40}}
        with closing(db.connect(self.path)) as conn:
            # Group "tmpl": this job plus seven earlier postings in other places.
            self.job(conn, 'now', 'Westbrook VIC', '2026-11-04T21:00:00+00:00', '2026-11-05T20:31:00+00:00', 'tmpl')
            for i in range(7):
                last = '2026-11-05T20:32:00+00:00' if i == 0 else f'2026-10-{20 + i:02d}T20:31:00+00:00'
                self.job(conn, f'old{i}', f'Synthetic Town {i} VIC', f'2026-10-{10 + i:02d}T21:00:00+00:00', last, 'tmpl')
            self.job(conn, 'solo', 'Melbourne VIC', '2026-11-01T21:00:00+00:00', '2026-11-02T20:31:00+00:00', 'solo-group')
            self.job(conn, 'raw', 'Melbourne VIC', '2026-11-01T21:00:00+00:00', '2026-11-05T20:31:00+00:00', None)
            conn.execute("INSERT INTO fetch_batches(batch_id,started_at,finished_at,collection_state,trigger) "
                         "VALUES ('b-ok',?,?,'success','synthetic'),"
                         "('b-later-partial','2026-11-06T20:30:00+00:00',NULL,'partial','synthetic')",
                         (BATCH_START, '2026-11-05T20:35:00+00:00'))
            conn.commit()

    def job(self, conn, ident, location, first, last, group):
        db.upsert_job(conn, dict(source='synthetic', source_id=ident, title='Synthetic Technician',
                                 company='Synthetic Schools', location=location,
                                 url='https://example.invalid/jobs/'+ident, listing_date='2026-10-30',
                                 description='Handwritten synthetic description '+ident))
        uid = 'synthetic:'+ident
        conn.execute('UPDATE jobs SET first_seen_at=?, last_seen_at=? WHERE uid=?', (first, last, uid))
        if group:
            db.set_dedupe(conn, uid, group, group)
        db.replace_decision(conn, uid, False)
        db.replace_score(conn, uid, 'T1', 0, 0, 0, 5, [], 1)

    def tearDown(self):
        self.temp.cleanup()

    def facts(self, uid):
        with closing(db.connect(self.path)) as conn:
            row = dict(conn.execute('SELECT * FROM jobs WHERE uid=?', (uid,)).fetchone())
            return row, history.for_job(conn, row)

    def test_facts_are_observations_bounded_and_newest_first(self):
        row, facts = self.facts('synthetic:now')
        # A later partial batch is not a reference point; the last full success is.
        self.assertEqual(facts['latest_successful_fetch_at'], BATCH_START)
        self.assertTrue(facts['seen_in_latest_successful_fetch'])
        self.assertEqual(facts['related_basis'], 'same_dedupe_group')
        self.assertEqual(facts['related_total'], 7)
        self.assertEqual(len(facts['related']), history.RELATED_LIMIT)
        self.assertEqual([r['uid'] for r in facts['related']],
                         [f'synthetic:old{i}' for i in (6, 5, 4, 3, 2)])
        self.assertNotIn('synthetic:now', [r['uid'] for r in facts['related']])
        self.assertTrue(all(r['seen_in_latest_successful_fetch'] is False for r in facts['related']))
        # Singleton group: zero others. Never analysed: unknown, not zero.
        self.assertEqual(self.facts('synthetic:solo')[1]['related_total'], 0)
        self.assertFalse(self.facts('synthetic:solo')[1]['seen_in_latest_successful_fetch'])
        raw = self.facts('synthetic:raw')[1]
        self.assertIsNone(raw['related_total'])
        self.assertIsNone(raw['related'])

    def test_database_without_successful_batches_reports_unknown(self):
        with closing(db.connect(self.path)) as conn:
            conn.execute('DELETE FROM fetch_batches'); conn.commit()
        _, facts = self.facts('synthetic:now')
        self.assertIsNone(facts['latest_successful_fetch_at'])
        self.assertIsNone(facts['seen_in_latest_successful_fetch'])
        self.assertTrue(all(r['seen_in_latest_successful_fetch'] is None for r in facts['related']))
        with closing(db.connect(self.path)) as conn:
            conn.execute('DROP TABLE fetch_batch_members'); conn.execute('DROP TABLE fetch_batches')
            self.assertIsNone(history.latest_successful_fetch(conn))

    def test_local_dates_use_melbourne_calendar(self):
        self.assertEqual(history.local_date('2026-11-04T21:00:00+00:00'), '2026-11-05')
        self.assertEqual(history.local_date('2026-10-30'), '2026-10-30')
        self.assertEqual(history.local_date(None), '')
        self.assertEqual(history.local_date('not a date at all'), 'not a date')

    def copied(self, uid, lang):
        with closing(db.connect(self.path)) as conn:
            row = panel._find(conn, self.config, uid)
            row['_history'] = history.for_job(conn, row)
        return panel_views._copy_text(row, lang)

    def test_copy_text_lists_located_facts_without_verdicts(self):
        zh = self.copied('synthetic:now', 'zh')
        self.assertIn('发布日期: 2026-10-30\n', zh)
        self.assertIn('本地首次 / 最后看到: 2026-11-05 / 2026-11-06（来源：synthetic）\n', zh)
        self.assertIn('本地最近一次成功抓取: 2026-11-06（本条在其中出现）\n', zh)
        self.assertIn('同一去重组（同公司、标题相近）的其他广告: 7 条\n', zh)
        self.assertIn('  · 首次看到 2026-10-17 · synthetic · Synthetic Town 6 VIC · 最后看到 2026-10-27，之后的抓取中未再出现\n', zh)
        self.assertIn('  · 另 2 条未列出\n', zh)
        self.assertIn('说明：基于本地记录分析，受搜索条件和抓取结果限制，建议自行核验岗位信息。\n', zh)
        # History sits between the listing fields and the full JD, which stays last.
        self.assertLess(zh.index('原帖链接'), zh.index('发布日期'))
        self.assertLess(zh.index('说明：'), zh.index('完整 JD'))
        self.assertTrue(zh.endswith('Handwritten synthetic description now\n'))
        en = self.copied('synthetic:now', 'en')
        self.assertIn('Other postings in the same duplicate group (same employer, similar title): 7\n', en)
        self.assertIn('last seen 2026-10-27, not seen in later fetches\n', en)
        for text in (zh, en):
            for line in text.split('\n'):
                if '·' in line and 'VIC' not in line and 'more' not in line and '另' not in line:
                    self.fail('related posting without a location: ' + line)
            history_part = text.split('完整 JD' if text is zh else 'Full job description')[0].lower()
            for word in VERDICT_WORDS:
                self.assertNotIn(word, history_part)
        unknown = self.copied('synthetic:raw', 'en')
        self.assertIn('similar title): unknown (not analysed yet)\n', unknown)
        # Without history (e.g. a caller that did not load it) the text is unchanged.
        with closing(db.connect(self.path)) as conn:
            plain = panel_views._copy_text(panel._find(conn, self.config, 'synthetic:now'), 'en')
        self.assertNotIn('Listing date', plain)

    def test_panel_detail_endpoint_copies_history(self):
        handler = type('SyntheticHandler', (panel.Handler,), {'config': self.config})
        server = panel.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            c = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
            c.request('GET', '/api/job?uid=' + quote('synthetic:now'), headers={'Cookie': 'jobs_lang=en'})
            body = json.loads(c.getresponse().read()); c.close()
        finally:
            server.shutdown(); server.server_close(); thread.join()
        self.assertIn('Other postings in the same duplicate group', body['html'])

    def test_api_detail_carries_history_and_list_pages_omit_it(self):
        store = api.SnapshotStore(clock=lambda: 100.)
        server = api.make_server(self.config, TOKEN, port=0, store=store)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()

        def get(path):
            c = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
            c.request('GET', path, headers={'Authorization': 'Bearer '+TOKEN})
            r = c.getresponse(); out = (r.status, json.loads(r.read())); c.close()
            return out
        try:
            status, page = get('/api/v1/jobs?limit=200')
            self.assertEqual(status, 200, page)
            self.assertTrue(page['items'])
            self.assertTrue(all('history' not in item and 'description' not in item for item in page['items']))
            sid = page['snapshot_id']
            # Later writes do not leak into a frozen snapshot's history.
            with closing(db.connect(self.path)) as conn:
                conn.execute("UPDATE jobs SET dedupe_group='moved' WHERE uid='synthetic:old6'"); conn.commit()
            status, frozen = get('/api/v1/jobs/' + quote('synthetic:now', safe='') + '?snapshot_id=' + sid)
            self.assertEqual(status, 200, frozen)
            self.assertEqual(frozen['item']['history']['related_total'], 7)
            self.assertEqual(frozen['item']['history']['related'][0]['uid'], 'synthetic:old6')
            status, fresh = get('/api/v1/jobs/' + quote('synthetic:now', safe=''))
            self.assertEqual(status, 200, fresh)
            item = fresh['item']['history']
            self.assertEqual(item['related_total'], 6)
            self.assertEqual(set(item['related'][0]), {'uid', 'source', 'title', 'location', 'listing_date',
                                                       'first_seen_at', 'last_seen_at',
                                                       'seen_in_latest_successful_fetch'})
            self.assertEqual(item['scope_note'], history.SCOPE_NOTE)
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main(verbosity=2)
