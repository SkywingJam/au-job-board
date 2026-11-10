"""Rendered panel HTML for plugin sidebars and switch:false pages (host API 2).

Synthetic data only. Checks: the plugin-less page is unchanged, sidebar markup and
page context, the List/Map switch lists only `switch` pages, the compare page mode,
map + sidebar together, escaping of plugin-supplied labels, the settings key hint.

Usage: .venv/bin/python tools/test_sidebar_panel.py
"""
from contextlib import closing
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import temp_cleanup  # noqa: E402
from jobs import db, i18n, panel_views, plugin_host  # noqa: E402

WORKSPACE = {'id': 'workspace', 'label': {'zh': '工作区', 'en': 'Workspace'},
             'handoff_label': {'zh': '加入工作区', 'en': 'Add to workspace'}, 'icon': 'workspace'}
MAP_PAGE = {'id': 'map', 'label': {'zh': '地图', 'en': 'Map'}, 'icon': 'map', 'switch': True}
COMPARE_PAGE = {'id': 'workspace', 'label': {'zh': '对比', 'en': 'Compare'}, 'icon': 'compare', 'switch': False}


def strip_assets(html: str) -> str:
    """Drop the inlined shared CSS/JS: they are the same for every page."""
    html = re.sub(r'<style>.*?</style>', '<style/>', html, flags=re.S)
    for name in ('panel.js', 'plugin_pages.js'):
        html = html.replace(panel_views.asset(name), name.upper())
    return html


def markup(html: str) -> str:
    """Markup outside <script> blocks (JSON data there is escaped by _js, not html)."""
    return re.sub(r'<script>.*?</script>', '', html, flags=re.S)


def page_context(html: str):
    found = re.search(r'window\.__PAGE_CONTEXT__=(.*?);</script>', html)
    return json.loads(found.group(1)) if found else None


class SidebarPanel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(temp_cleanup.cleanup, self.tmp)
        root = Path(self.tmp.name)
        (root / 'skills.yaml').write_text('version: 1\nskills: []\n')
        self.config = {'db_path': str(root / 'jobs.db'), 'skills_path': str(root / 'skills.yaml')}
        self.conn = db.connect(self.config['db_path'])
        self.addCleanup(self.conn.close)
        for n in (1, 2):
            db.upsert_job(self.conn, {'source': 'synthetic', 'source_id': str(n), 'title': f'Synthetic Role {n}',
                                      'company': 'Example Co', 'location': 'Melbourne VIC 3000',
                                      'description': 'Python.', 'url': f'https://example.invalid/{n}'})
            db.replace_decision(self.conn, f'synthetic:{n}', 0)
            db.replace_score(self.conn, f'synthetic:{n}', 'T1', 3, 0, 0, 5, '[]', 5)
        self.conn.commit()

    def render(self, lang='en', **kw):
        return panel_views.render_page(self.conn, self.config, 'all', 0, 40, None, {}, lang, None, 'auto', **kw)

    def test_inert_without_plugins(self):
        for lang in ('zh', 'en'):
            default = self.render(lang)
            self.assertEqual(default, self.render(lang, display_pages=(), sidebars=(), agent_access='on'))
            visible = strip_assets(default)
            for marker in ('__PAGE_CONTEXT__', '__HOST_I18N__', 'PLUGIN_PAGES.JS', 'plugin-sidebar', 'sidebar-toggle',
                           'has-sidebar', 'plugin-solo', 'display-switch', 'draggable', 'data-handoff'):
                self.assertNotIn(marker, visible, marker)
        # Host strings never join the always-injected page dictionary.
        self.assertFalse([k for k in i18n.js_strings('en') if k.startswith('sidebar.')])
        self.assertEqual(set(i18n.host_strings('zh')), set(i18n.host_strings('en')))

    def test_sidebar_markup_and_context(self):
        html = self.render('en', sidebars=[WORKSPACE], agent_access='on')
        visible = strip_assets(html)
        self.assertIn('<body class="has-sidebar">', visible)
        self.assertIn('id="sidebar-toggle" aria-expanded="false" aria-controls="plugin-sidebar"', visible)
        self.assertIn('aria-label="Workspace, 0 jobs"', visible)
        self.assertIn('<aside class="plugin-sidebar" id="plugin-sidebar" aria-label="Workspace"', visible)
        self.assertIn('<iframe id="plugin-sidebar-frame" title="Workspace" sandbox="allow-scripts" referrerpolicy="origin" '
                      'data-plugin-src="/plugins/workspace/frame?slot=sidebar"></iframe>', visible)
        self.assertNotIn(' src=', visible.split('id="plugin-sidebar-frame"')[1].split('</iframe>')[0], 'loads lazily')
        self.assertIn('role="separator" aria-orientation="vertical" tabindex="0" aria-label="Resize Workspace"', visible)
        self.assertIn('aria-label="Close Workspace"', visible)
        self.assertIn('PLUGIN_PAGES.JS', visible)
        self.assertNotIn('display-switch', visible, 'no page plugin: no List/Map switch')
        context = page_context(html)
        self.assertEqual(context['sidebars'], [WORKSPACE])
        self.assertEqual(context['agent_access'], 'on')
        self.assertEqual([context['display'], context['plugin_id'], context['pages']], ['list', None, []])
        strings = json.loads(re.search(r'window\.__HOST_I18N__=(.*?);</script>', html).group(1))
        self.assertEqual(strings['sidebar.added'], 'Added to {label}')
        self.assertEqual(page_context(self.render('en', sidebars=[WORKSPACE], agent_access='maybe'))['agent_access'], 'off')
        zh = strip_assets(self.render('zh', sidebars=[WORKSPACE]))
        self.assertIn('aria-label="工作区，0 个岗位"', zh)
        self.assertIn('收起工作区', zh)

    def test_switch_filtering(self):
        legacy_map = {k: v for k, v in MAP_PAGE.items() if k != 'switch'}   # missing key = in the switch
        for pages in ([MAP_PAGE, COMPARE_PAGE], [legacy_map, COMPARE_PAGE]):
            html = strip_assets(self.render('en', display_pages=pages, sidebars=[WORKSPACE]))
            switch = re.search(r'<div class="display-switch".*?</div>', html).group(0)
            self.assertEqual(re.findall(r'data-plugin-display="([^"]+)"', switch), ['list', 'map'])
            self.assertEqual([p['id'] for p in page_context(self.render('en', display_pages=pages, sidebars=[WORKSPACE]))['pages']],
                             ['map', 'workspace'], 'the compare page stays a valid display')
        only_compare = strip_assets(self.render('en', display_pages=[COMPARE_PAGE], sidebars=[WORKSPACE]))
        self.assertNotIn('display-switch', only_compare)

    def test_compare_page_mode(self):
        html = self.render('en', display='workspace', display_pages=[MAP_PAGE, COMPARE_PAGE], sidebars=[WORKSPACE])
        visible = strip_assets(html)
        self.assertIn('<body class="plugin-display plugin-solo">', visible)
        self.assertIn('<button type="button" id="plugin-page-back" data-plugin-back>← Back to list</button>', visible)
        self.assertIn('<iframe id="plugin-display-frame" title="Compare" data-plugin-src="/plugins/workspace/frame?slot=page" '
                      'sandbox="allow-scripts" referrerpolicy="origin"></iframe>', visible)
        self.assertIn('id="plugin-detail-back" onclick="closeDetail()">← Back to Compare</button>', visible)
        self.assertNotIn('id="sidebar-toggle"', visible)
        self.assertNotIn('id="plugin-sidebar"', visible)
        self.assertEqual(markup(visible).count('← Back to list'), 1, 'one core back control')
        context = page_context(html)
        self.assertEqual([context['display'], context['plugin_id']], ['workspace', 'workspace'])
        self.assertIn('__HOST_I18N__', html)
        zh = strip_assets(self.render('zh', display='workspace', display_pages=[COMPARE_PAGE], sidebars=[WORKSPACE]))
        self.assertIn('>← 返回列表</button>', zh)
        self.assertIn('>← 返回对比</button>', zh)

    def test_map_with_sidebar(self):
        html = strip_assets(self.render('en', display='map', display_pages=[MAP_PAGE, COMPARE_PAGE], sidebars=[WORKSPACE]))
        self.assertIn('<body class="plugin-display has-sidebar">', html)
        self.assertIn('data-plugin-src="/plugins/map/frame" sandbox="allow-scripts"', html)
        self.assertIn('>← Back to map</button>', html)
        self.assertIn('id="sidebar-toggle"', html)
        self.assertIn('data-plugin-src="/plugins/workspace/frame?slot=sidebar"', html)
        self.assertNotIn('plugin-page-back', html)

    def test_map_only_markup_unchanged(self):
        # Without sidebars or switch:false pages the map page markup stays as before:
        # no host strings, no sidebar, same frame and back button.
        html = strip_assets(self.render('zh', display='map', display_pages=[MAP_PAGE]))
        self.assertIn('<body class="plugin-display">', html)
        self.assertIn('<iframe id="plugin-display-frame" title="地图" data-plugin-src="/plugins/map/frame" sandbox="allow-scripts" '
                      'referrerpolicy="origin"></iframe><button type="button" id="plugin-detail-back" onclick="closeDetail()">'
                      '← 返回地图</button>', html)
        self.assertNotIn('__HOST_I18N__', html)
        self.assertNotIn('sidebar-toggle', html)

    def test_plugin_labels_are_escaped(self):
        hostile = {**WORKSPACE, 'label': {'en': '<img src=x onerror=alert(1)>', 'zh': '"x'}}
        html = markup(strip_assets(self.render('en', sidebars=[hostile])))
        self.assertNotIn('<img src=x', html)
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', html)
        context = page_context(self.render('en', sidebars=[hostile]))
        self.assertEqual(context['sidebars'][0]['label']['en'], '<img src=x onerror=alert(1)>')

    def test_settings_key_hint_only_with_sidebar(self):
        def settings():
            return panel_views.render_settings(self.conn, self.config, 'en', None, 'display', 'auto')
        hint = i18n.t('en', 'sidebar.keys_hint')
        self.assertNotIn(hint, settings())
        with patch.object(plugin_host, 'sidebar_plugins', return_value=[WORKSPACE]):
            self.assertIn(hint, settings())
        with patch.object(plugin_host, 'sidebar_plugins', side_effect=OSError('unreadable')):
            self.assertNotIn(hint, settings())


if __name__ == '__main__':
    unittest.main(verbosity=1)
