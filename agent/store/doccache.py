"""
共享文档缓存（P2，对应方案 §7.1 / §8.2）

问题：旧流程每分析一个主题就重抓一遍全报。两个试点主题 = 两次全量采集，
既浪费请求预算，也让"同一期"在不同主题下可能拿到不同快照。

方案 §7.1 要求："不要在每个关键词分析时重新抓全报。先建立共享文档缓存，
再按主题分析新增与修订内容。读取全文、去重和语义抽取应复用文档版本缓存。"

本模块提供：
  - 按 URL / 期号缓存原始文档，多主题共享同一份
  - 内容哈希变化时**新增一个版本**，而不是覆盖 —— 修订可被检出（增量采集）
  - `first_seen_at` 与 `published_at` 严格分开：今天补采一份旧文件，
    不等于系统当年就发现了它（§8.2）
  - 保存 ETag / Last-Modified，供条件请求使用

缓存不是可有可无的加速器：它是"同一期在不同主题下是同一份证据"的保证。
"""

import datetime
import hashlib
import json
import os
import sqlite3

CACHE_DIR = os.path.expanduser('~/.rmrb_sentinel')
CACHE_PATH = os.path.join(CACHE_DIR, 'doccache.db')

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    url TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    content_hash TEXT,
    payload_json TEXT NOT NULL,
    etag TEXT,
    last_modified TEXT,
    published_at TEXT,
    first_seen_at TEXT NOT NULL,
    last_fetched_at TEXT NOT NULL,
    fetch_count INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS document_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    version INTEGER NOT NULL,
    content_hash TEXT,
    payload_json TEXT NOT NULL,
    observed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_doc_kind ON documents(kind);
CREATE INDEX IF NOT EXISTS idx_docver_url ON document_versions(url, version);
"""


def _conn():
    os.makedirs(CACHE_DIR, exist_ok=True)
    conn = sqlite3.connect(CACHE_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _hash(payload: dict) -> str:
    text = payload.get('content') or json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                                default=str)
    return hashlib.sha256(str(text).encode('utf-8')).hexdigest()[:16]


class DocumentCache:
    """共享文档缓存。多个主题、多次运行复用同一份文档版本。"""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.stats = {'hits': 0, 'misses': 0, 'stores': 0, 'new_versions': 0}

    # ── 单篇文档 ────────────────────────────────────────────
    def get_document(self, url: str, as_of: str = None) -> dict | None:
        if not self.enabled:
            return None
        conn = _conn()
        row = conn.execute('SELECT * FROM documents WHERE url = ?', (url,)).fetchone()
        if row and as_of:
            # 历史回放只能读取当时已经观察到的版本。发布日期更早不能证明
            # 系统在 as_of 那天已经发现过后来的修订正文。
            version = conn.execute(
                'SELECT payload_json, version, observed_at FROM document_versions '
                'WHERE url = ? AND substr(observed_at, 1, 10) <= ? '
                'ORDER BY version DESC LIMIT 1',
                (url, f'{as_of[:4]}-{as_of[4:6]}-{as_of[6:]}'),
            ).fetchone()
            if not version:
                conn.close()
                self.stats['misses'] += 1
                return None
            payload = json.loads(version['payload_json'])
            payload['cache_version'] = version['version']
            payload['from_cache'] = True
            conn.close()
            self.stats['hits'] += 1
            return payload
        conn.close()
        if not row:
            self.stats['misses'] += 1
            return None
        self.stats['hits'] += 1
        payload = json.loads(row['payload_json'])
        # first_seen_at 由缓存持有，采集时点不覆盖它。
        payload['first_seen_at'] = row['first_seen_at']
        payload['cache_version'] = row['version']
        payload['from_cache'] = True
        return payload

    def put_document(self, url: str, payload: dict, kind: str = 'policy_doc',
                     etag: str = None, last_modified: str = None) -> dict:
        """
        写入或更新文档。内容哈希变化 → 版本 +1，旧版本留档（修订可被检出）。
        返回 {'action': 'inserted'|'unchanged'|'revised', 'version': n}
        """
        if not self.enabled:
            return {'action': 'disabled', 'version': 0}
        now = datetime.datetime.now().isoformat()
        chash = payload.get('content_hash') or _hash(payload)
        conn = _conn()
        row = conn.execute('SELECT * FROM documents WHERE url = ?', (url,)).fetchone()

        if not row:
            conn.execute(
                'INSERT INTO documents (url, kind, content_hash, payload_json, etag, '
                'last_modified, published_at, first_seen_at, last_fetched_at, '
                'fetch_count, version) VALUES (?,?,?,?,?,?,?,?,?,1,1)',
                (url, kind, chash, json.dumps(payload, ensure_ascii=False, default=str),
                 etag, last_modified, payload.get('published_at'), now, now))
            conn.execute(
                'INSERT INTO document_versions (url, version, content_hash, payload_json, '
                'observed_at) VALUES (?,1,?,?,?)',
                (url, chash, json.dumps(payload, ensure_ascii=False, default=str), now))
            action, version = 'inserted', 1
            self.stats['stores'] += 1
        elif row['content_hash'] == chash:
            conn.execute(
                'UPDATE documents SET last_fetched_at = ?, fetch_count = fetch_count + 1 '
                'WHERE url = ?', (now, url))
            action, version = 'unchanged', row['version']
        else:
            version = row['version'] + 1
            conn.execute(
                'UPDATE documents SET content_hash=?, payload_json=?, etag=?, '
                'last_modified=?, published_at=?, last_fetched_at=?, '
                'fetch_count = fetch_count + 1, version=? WHERE url=?',
                (chash, json.dumps(payload, ensure_ascii=False, default=str), etag,
                 last_modified, payload.get('published_at'), now, version, url))
            conn.execute(
                'INSERT INTO document_versions (url, version, content_hash, payload_json, '
                'observed_at) VALUES (?,?,?,?,?)',
                (url, version, chash,
                 json.dumps(payload, ensure_ascii=False, default=str), now))
            action = 'revised'
            self.stats['new_versions'] += 1

        conn.commit()
        conn.close()
        return {'action': action, 'version': version}

    # ── 整期人民日报 ───────────────────────────────────────
    def get_issue(self, date: str, as_of: str = None) -> dict | None:
        """取整期缓存。多个主题共享同一期采集结果（§7.1「各主题共享抓取结果」）。"""
        return self.get_document(f'rmrb_issue:{date}', as_of=as_of)

    def put_issue(self, date: str, summary: dict) -> dict:
        return self.put_document(f'rmrb_issue:{date}', summary, kind='rmrb_issue')

    # ── 条件请求 ───────────────────────────────────────────
    def conditional_headers(self, url: str) -> dict:
        """取该 URL 上次的 ETag / Last-Modified，用于增量抓取。"""
        if not self.enabled:
            return {}
        conn = _conn()
        row = conn.execute(
            'SELECT etag, last_modified FROM documents WHERE url = ?', (url,)).fetchone()
        conn.close()
        if not row:
            return {}
        headers = {}
        if row['etag']:
            headers['if-none-match'] = row['etag']
        if row['last_modified']:
            headers['if-modified-since'] = row['last_modified']
        return headers

    def revisions_since(self, url: str, version: int) -> list[dict]:
        """列出某文档在给定版本之后的修订，用于变化检测。"""
        conn = _conn()
        rows = conn.execute(
            'SELECT version, content_hash, observed_at FROM document_versions '
            'WHERE url = ? AND version > ? ORDER BY version ASC', (url, version)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def report(self) -> dict:
        total = self.stats['hits'] + self.stats['misses']
        return {
            **self.stats,
            'hit_rate': round(self.stats['hits'] / total, 3) if total else None,
            'note': '缓存命中说明这一份文档没有重复抓取；新版本数说明检出了多少次修订。',
        }
