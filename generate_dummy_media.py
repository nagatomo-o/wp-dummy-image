from __future__ import annotations

import argparse
import csv
import re
import sys
import urllib.parse
from pathlib import Path, PurePosixPath

from PIL import Image

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

_UPLOADS_PATH = re.compile(
    r"(?<![A-Za-z0-9])uploads/([^?\s\"'<>\\]+)",
    re.IGNORECASE,
)
_TRAILING_PATH_CHARS = ".,);]"


class PhpSerializeError(Exception):
    """PHP serialize を解釈できない。"""


class GenerateError(Exception):
    """生成を開始できない。"""


class _Parser:
    def __init__(self, data: str) -> None:
        self.data = data.encode("utf-8")
        self.index = 0

    def parse(self):
        value = self._value()
        if self.index != len(self.data):
            raise PhpSerializeError("未消費のデータがあります")
        return value

    def _peek(self) -> str:
        if self.index >= len(self.data):
            raise PhpSerializeError("データが途中で終わっています")
        return chr(self.data[self.index])

    def _expect(self, text: str) -> None:
        raw = text.encode("ascii")
        end = self.index + len(raw)
        if self.data[self.index:end] != raw:
            raise PhpSerializeError(f"想定外の文字です: {text}")
        self.index = end

    def _read_until(self, stop: str) -> str:
        marker = stop.encode("ascii")
        end = self.data.find(marker, self.index)
        if end < 0:
            raise PhpSerializeError("区切りがありません")
        chunk = self.data[self.index:end].decode("ascii")
        self.index = end + len(marker)
        return chunk

    def _value(self):
        kind = self._peek()
        if kind == "N":
            self._expect("N;")
            return None
        if kind == "b":
            self._expect("b:")
            return self._read_until(";") == "1"
        if kind == "i":
            self._expect("i:")
            return int(self._read_until(";"))
        if kind == "d":
            self._expect("d:")
            return float(self._read_until(";"))
        if kind == "s":
            self._expect("s:")
            length = int(self._read_until(":"))
            self._expect('"')
            end = self.index + length
            if end > len(self.data):
                raise PhpSerializeError("文字列が短すぎます")
            text = self.data[self.index:end].decode("utf-8")
            self.index = end
            self._expect('";')
            return text
        if kind == "a":
            self._expect("a:")
            count = int(self._read_until(":"))
            self._expect("{")
            result = {}
            for _ in range(count):
                key = self._value()
                result[key] = self._value()
            self._expect("}")
            return result
        raise PhpSerializeError(f"未対応の型です: {kind}")


def php_unserialize(data: str):
    return _Parser(data).parse()


def uploads_paths(text: str) -> list[str]:
    normalized = text.replace("\\/", "/")
    found: list[str] = []
    seen: set[str] = set()
    for match in _UPLOADS_PATH.findall(normalized):
        relative = urllib.parse.unquote(match).replace("\\", "/").strip("/")
        relative = relative.rstrip(_TRAILING_PATH_CHARS)
        if not relative or relative in seen:
            continue
        if any(part == ".." for part in PurePosixPath(relative).parts):
            continue
        seen.add(relative)
        found.append(relative)
    return found


def _normalize_relative(path: str) -> str:
    return path.replace("\\", "/").strip().strip("/")


COLOR = (127, 127, 127)
JPEG_QUALITY = 75
MAX_DIMENSION = 8000
IMAGE_FORMATS = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".gif": "GIF",
    ".ico": "ICO",
}
REQUIRED_COLUMNS = ("file_path", "mime_type", "metadata_raw")
DEFAULT_SIZE = 32


def _dimension(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def attachment_targets(file_path: str, mime_type: str, metadata_raw: str) -> list[dict]:
    is_image = mime_type.startswith("image/")
    width = None
    height = None
    sizes: list[dict] = []
    parsed = True
    raw = metadata_raw.strip()
    if raw and raw != "NULL":
        try:
            data = php_unserialize(raw)
        except (PhpSerializeError, ValueError, UnicodeDecodeError):
            parsed = False
            data = None
        if not parsed:
            pass
        elif not isinstance(data, dict):
            parsed = False
        else:
            width = _dimension(data.get("width"))
            height = _dimension(data.get("height"))
            raw_sizes = data.get("sizes")
            if isinstance(raw_sizes, dict):
                for item in raw_sizes.values():
                    if not isinstance(item, dict):
                        continue
                    name = item.get("file")
                    sizes.append(
                        {
                            "file": name if isinstance(name, str) else "",
                            "width": _dimension(item.get("width")),
                            "height": _dimension(item.get("height")),
                        }
                    )
    if is_image and (width is None or height is None):
        width = DEFAULT_SIZE
        height = DEFAULT_SIZE
    targets = [
        {
            "relative_path": file_path,
            "kind": "image" if is_image else "empty",
            "width": width if is_image else None,
            "height": height if is_image else None,
        }
    ]
    if not (parsed and is_image):
        return targets
    parent = PurePosixPath(file_path.replace("\\", "/")).parent
    parent_text = "" if str(parent) == "." else parent.as_posix()
    for size in sizes:
        name = size["file"]
        relative = f"{parent_text}/{name}" if parent_text else name
        targets.append(
            {
                "relative_path": relative,
                "kind": "image",
                "width": DEFAULT_SIZE if size["width"] is None else size["width"],
                "height": DEFAULT_SIZE if size["height"] is None else size["height"],
            }
        )
    return targets


def safe_output_path(out_dir: Path, relative_path: str) -> Path | None:
    text = relative_path.strip()
    if not text:
        return None
    normalized = text.replace("\\", "/")
    if normalized.startswith("/") or (len(normalized) >= 2 and normalized[1] == ":"):
        return None
    path = PurePosixPath(normalized)
    if any(part == ".." for part in path.parts):
        return None
    root = out_dir.resolve()
    destination = root.joinpath(*path.parts)
    resolved = destination.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    if resolved == root:
        return None
    return destination


def write_empty(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def write_image(path: Path, width: int, height: int) -> bool:
    if width < 1 or height < 1 or width > MAX_DIMENSION or height > MAX_DIMENSION:
        return False
    image_format = IMAGE_FORMATS.get(path.suffix.lower())
    if image_format is None:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (width, height), COLOR)
    if image_format == "JPEG":
        image.save(path, format=image_format, quality=JPEG_QUALITY)
    elif image_format == "GIF":
        palette = Image.new("P", (width, height))
        palette.putpalette(list(COLOR) + [0] * (768 - 3))
        palette.save(path, format="GIF")
    elif image_format == "ICO":
        image.save(path, format="ICO", sizes=[(width, height)])
    else:
        image.save(path, format=image_format)
    return True


def _read_csv_rows(csv_path: Path, required: tuple[str, ...]) -> list[dict]:
    try:
        handle = csv_path.open(encoding="utf-8", newline="")
    except OSError as exc:
        raise GenerateError("CSVを開けません") from exc
    with handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        if any(name not in fieldnames for name in required):
            raise GenerateError("必要な列がありません")
        return list(reader)


def _option_target(relative_path: str) -> dict:
    suffix = PurePosixPath(relative_path).suffix.lower()
    if suffix in IMAGE_FORMATS:
        return {
            "relative_path": relative_path,
            "kind": "image",
            "width": DEFAULT_SIZE,
            "height": DEFAULT_SIZE,
        }
    return {
        "relative_path": relative_path,
        "kind": "empty",
        "width": None,
        "height": None,
    }


def generate(
    csv_path: Path,
    out_dir: Path,
    options_path: Path | None = None,
) -> tuple[int, int, int]:
    rows = _read_csv_rows(csv_path, REQUIRED_COLUMNS)
    option_paths: list[str] = []
    if options_path is not None:
        seen: set[str] = set()
        for row in _read_csv_rows(options_path, ("option_value",)):
            for relative in uploads_paths(row.get("option_value") or ""):
                if relative in seen:
                    continue
                seen.add(relative)
                option_paths.append(relative)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise GenerateError("出力先を作れません") from exc
    images = 0
    empties = 0
    skips = 0
    written: set[str] = set()
    for row in rows:
        targets = attachment_targets(
            row.get("file_path") or "",
            row.get("mime_type") or "",
            row.get("metadata_raw") or "",
        )
        for target in targets:
            kind = _write_target(out_dir, target)
            if kind == "image":
                images += 1
                written.add(_normalize_relative(target["relative_path"]))
            elif kind == "empty":
                empties += 1
                written.add(_normalize_relative(target["relative_path"]))
            else:
                skips += 1
    for relative in option_paths:
        if relative in written:
            continue
        kind = _write_target(out_dir, _option_target(relative))
        if kind == "image":
            images += 1
        elif kind == "empty":
            empties += 1
        else:
            skips += 1
    return images, empties, skips


def _write_target(out_dir: Path, target: dict) -> str:
    destination = safe_output_path(out_dir, target["relative_path"])
    if destination is None:
        return "skip"
    if target["kind"] == "empty":
        write_empty(destination)
        return "empty"
    if write_image(destination, target["width"], target["height"]):
        return "image"
    return "skip"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="添付ファイル一覧からダミーメディアを作ります")
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--options", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        images, empties, skips = generate(args.csv, args.out, args.options)
    except GenerateError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"画像: {images}")
    print(f"空ファイル: {empties}")
    print(f"スキップ: {skips}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
