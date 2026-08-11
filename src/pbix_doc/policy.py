from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MetadataExclusionPolicy:
    table_prefixes: tuple[str, ...]

    def excludes(self, value: str | None) -> bool:
        table = self.table_name(value)
        return table.casefold().startswith(self.table_prefixes)

    @staticmethod
    def table_name(value: str | None) -> str:
        text = str(value or "").strip()
        if "[" in text:
            text = text.split("[", 1)[0].rstrip()
        if len(text) >= 2 and text.startswith("'") and text.endswith("'"):
            text = text[1:-1].replace("''", "'")
        return text.strip()


METADATA_EXCLUSION_POLICY = MetadataExclusionPolicy(
    ("local", "datetabletemplate", "h$", "r$", "u$")
)
