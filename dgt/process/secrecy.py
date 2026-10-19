"""涉密识别。档级取案卷密级，文件级取文献密级，目录级按关键词识别。"""
from __future__ import annotations


class SecrecyJudge:
    def __init__(self, cfg: dict):
        self.secret = cfg.get("secret_words", [])
        self.public = cfg.get("public_words", [])
        self.keywords = cfg.get("catalog_keywords") or []
        self.scope = cfg.get("scope", "any")
        self.empty_as = cfg.get("empty_as", "secret")

    def level_is_secret(self, value: str | None) -> bool | None:
        """True 涉密，False 不涉密，None 无密级信息。"""
        v = (value or "").strip()
        if not v:
            return None
        if any(w in v for w in self.public):
            return False
        if any(w in v for w in self.secret):
            return True
        return None

    def catalog_hit(self, *texts: str | None) -> list[str]:
        return [k for k in self.keywords if any(k in (t or "") for t in texts)]

    def judge(self, archive_level: str | None, doc_level: str | None, project_name: str | None,
              title: str | None) -> tuple[str, str]:
        """返回 (涉密标记, 说明)。涉密标记为 涉密 或 非涉密。"""
        a = self.level_is_secret(archive_level)
        d = self.level_is_secret(doc_level)
        hits = self.catalog_hit(project_name, title)
        notes = []
        if hits:
            return "涉密", f"目录级涉密关键词 {'、'.join(hits)}"
        if self.scope in ("any", "archive") and a:
            notes.append(f"档级密级 {archive_level}")
        if self.scope in ("any", "file") and d:
            notes.append(f"文件密级 {doc_level}")
        if notes:
            return "涉密", "；".join(notes)
        relevant = {"any": (a, d), "archive": (a,), "file": (d,)}[self.scope]
        if all(x is None for x in relevant):
            if self.empty_as == "secret":
                return "涉密", "无密级信息，按涉密从严处理"
            return "非涉密", "无密级信息，按非涉密处理"
        return "非涉密", "密级为不涉密"
