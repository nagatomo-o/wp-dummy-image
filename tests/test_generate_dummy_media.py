import contextlib
import csv
import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from generate_dummy_media import (
    COLOR,
    GenerateError,
    PhpSerializeError,
    attachment_targets,
    generate,
    main,
    php_unserialize,
    safe_output_path,
    uploads_paths,
    write_empty,
    write_image,
)


class PhpSerializeTests(unittest.TestCase):
    def test_reads_width_height_and_size_file(self):
        metadata = (
            'a:3:{s:5:"width";i:1900;s:6:"height";i:650;s:5:"sizes";a:1:{'
            's:9:"thumbnail";a:3:{s:4:"file";s:17:"sl1-1-150x150.jpg";'
            's:5:"width";i:150;s:6:"height";i:150;}}}'
        )
        parsed = php_unserialize(metadata)
        self.assertEqual(parsed["width"], 1900)
        self.assertEqual(parsed["height"], 650)
        self.assertEqual(parsed["sizes"]["thumbnail"]["file"], "sl1-1-150x150.jpg")
        self.assertEqual(parsed["sizes"]["thumbnail"]["width"], 150)
        self.assertEqual(parsed["sizes"]["thumbnail"]["height"], 150)

    def test_reads_scalar_types_and_utf8_byte_length(self):
        self.assertIsNone(php_unserialize("N;"))
        self.assertIs(php_unserialize("b:1;"), True)
        self.assertIs(php_unserialize("b:0;"), False)
        self.assertEqual(php_unserialize("i:-3;"), -3)
        self.assertEqual(php_unserialize("d:1.5;"), 1.5)
        self.assertEqual(php_unserialize('s:3:"あ";'), "あ")
        self.assertEqual(php_unserialize('s:3:"a"b";'), 'a"b')
        self.assertEqual(php_unserialize("a:0:{}"), {})

    def test_rejects_unsupported_type(self):
        with self.assertRaises(PhpSerializeError):
            php_unserialize('O:1:"x":0:{}')


class AttachmentTargetTests(unittest.TestCase):
    def test_original_and_resized_file_share_directory(self):
        metadata = (
            'a:3:{s:5:"width";i:1900;s:6:"height";i:650;s:5:"sizes";a:1:{'
            's:9:"thumbnail";a:3:{s:4:"file";s:17:"sl1-1-150x150.jpg";'
            's:5:"width";i:150;s:6:"height";i:150;}}}'
        )
        targets = attachment_targets("2018/10/sl1-1.jpg", "image/jpeg", metadata)
        self.assertEqual(
            targets[0],
            {
                "relative_path": "2018/10/sl1-1.jpg",
                "kind": "image",
                "width": 1900,
                "height": 650,
            },
        )
        self.assertEqual(
            targets[1],
            {
                "relative_path": "2018/10/sl1-1-150x150.jpg",
                "kind": "image",
                "width": 150,
                "height": 150,
            },
        )

    def test_backslash_directory_is_normalized(self):
        metadata = (
            'a:3:{s:5:"width";i:10;s:6:"height";i:20;s:5:"sizes";a:1:{'
            's:9:"thumbnail";a:3:{s:4:"file";s:8:"tiny.jpg";'
            's:5:"width";i:10;s:6:"height";i:10;}}}'
        )
        targets = attachment_targets("2018\\10\\a.jpg", "image/jpeg", metadata)
        self.assertEqual(targets[1]["relative_path"], "2018/10/tiny.jpg")

    def test_missing_metadata_uses_32_square(self):
        targets = attachment_targets("2018/11/favicon.ico", "image/x-icon", "NULL")
        self.assertEqual(
            targets,
            [
                {
                    "relative_path": "2018/11/favicon.ico",
                    "kind": "image",
                    "width": 32,
                    "height": 32,
                }
            ],
        )

    def test_blank_metadata_has_no_sizes(self):
        targets = attachment_targets("2018/11/a.jpg", "image/jpeg", "  ")
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["width"], 32)
        self.assertEqual(targets[0]["height"], 32)

    def test_broken_metadata_keeps_original_only(self):
        targets = attachment_targets("2018/11/a.jpg", "image/jpeg", "not-serialized")
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["kind"], "image")
        self.assertEqual(targets[0]["width"], 32)
        self.assertEqual(targets[0]["height"], 32)

    def test_size_without_dimensions_is_32_square(self):
        metadata = (
            'a:3:{s:5:"width";i:10;s:6:"height";i:20;s:5:"sizes";a:1:{'
            's:9:"thumbnail";a:1:{s:4:"file";s:5:"t.jpg";}}}'
        )
        targets = attachment_targets("a.jpg", "image/jpeg", metadata)
        self.assertEqual(targets[1]["relative_path"], "t.jpg")
        self.assertEqual(targets[1]["width"], 32)
        self.assertEqual(targets[1]["height"], 32)

    def test_non_image_is_empty_target(self):
        targets = attachment_targets("2019/06/a.pdf", "application/pdf", "NULL")
        self.assertEqual(
            targets,
            [
                {
                    "relative_path": "2019/06/a.pdf",
                    "kind": "empty",
                    "width": None,
                    "height": None,
                }
            ],
        )


class OutputPathTests(unittest.TestCase):
    def test_joins_relative_path_under_output_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory)
            destination = safe_output_path(out_dir, "2018/10/a.jpg")
            self.assertEqual(
                destination,
                out_dir.resolve().joinpath("2018", "10", "a.jpg"),
            )

    def test_rejects_parent_absolute_and_blank_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory)
            self.assertIsNone(safe_output_path(out_dir, "../x.jpg"))
            self.assertIsNone(safe_output_path(out_dir, "2018/../../x.jpg"))
            self.assertIsNone(safe_output_path(out_dir, "/tmp/x.jpg"))
            self.assertIsNone(safe_output_path(out_dir, "C:/Windows/x.jpg"))
            self.assertIsNone(safe_output_path(out_dir, ""))
            self.assertIsNone(safe_output_path(out_dir, "   "))


class ImageWriteTests(unittest.TestCase):
    def test_png_gif_and_ico_use_exact_pale_color(self):
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory)
            self.assertTrue(write_image(out_dir / "a.png", 8, 4))
            self.assertTrue(write_image(out_dir / "a.gif", 8, 4))
            self.assertTrue(write_image(out_dir / "a.ico", 32, 32))
            for name, size in (("a.png", (8, 4)), ("a.gif", (8, 4)), ("a.ico", (32, 32))):
                image = Image.open(out_dir / name).convert("RGB")
                self.assertEqual(image.size, size)
                self.assertEqual(image.getpixel((0, 0)), COLOR)

    def test_jpeg_is_solid_at_requested_size(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.jpg"
            self.assertTrue(write_image(path, 8, 4))
            image = Image.open(path).convert("RGB")
            self.assertEqual(image.size, (8, 4))
            self.assertEqual(len(image.getcolors(2)), 1)

    def test_rejects_out_of_range_dimensions_and_unknown_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory)
            self.assertTrue(write_image(out_dir / "edge.jpg", 8000, 1))
            self.assertFalse(write_image(out_dir / "wide.jpg", 8001, 1))
            self.assertFalse(write_image(out_dir / "zero.jpg", 0, 10))
            self.assertFalse(write_image(out_dir / "a.webp", 8, 4))
            self.assertFalse((out_dir / "wide.jpg").exists())
            self.assertFalse((out_dir / "a.webp").exists())

    def test_empty_file_is_zero_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "2019" / "06" / "a.pdf"
            write_empty(path)
            self.assertEqual(path.read_bytes(), b"")


class GenerateTests(unittest.TestCase):
    def _write_csv(self, path: Path, rows: list[dict]) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["file_path", "mime_type", "metadata_raw"],
            )
            writer.writeheader()
            writer.writerows(rows)

    def test_writes_original_resized_and_empty_pdf(self):
        metadata = (
            'a:3:{s:5:"width";i:1900;s:6:"height";i:650;s:5:"sizes";a:1:{'
            's:9:"thumbnail";a:3:{s:4:"file";s:17:"sl1-1-150x150.jpg";'
            's:5:"width";i:150;s:6:"height";i:150;}}}'
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "media.csv"
            out_dir = root / "uploads"
            self._write_csv(
                csv_path,
                [
                    {
                        "file_path": "2018/10/sl1-1.jpg",
                        "mime_type": "image/jpeg",
                        "metadata_raw": metadata,
                    },
                    {
                        "file_path": "2019/06/a.pdf",
                        "mime_type": "application/pdf",
                        "metadata_raw": "NULL",
                    },
                ],
            )
            marker = out_dir / "2018" / "10" / "sl1-1.jpg"
            marker.parent.mkdir(parents=True)
            marker.write_bytes(b"old")
            images, empties, skips = generate(csv_path, out_dir)
            self.assertEqual((images, empties, skips), (2, 1, 0))
            self.assertNotEqual(marker.read_bytes(), b"old")
            self.assertEqual(Image.open(out_dir / "2018/10/sl1-1.jpg").size, (1900, 650))
            self.assertEqual(
                Image.open(out_dir / "2018/10/sl1-1-150x150.jpg").size,
                (150, 150),
            )
            self.assertEqual((out_dir / "2019/06/a.pdf").read_bytes(), b"")

    def test_skips_only_the_rejected_file_in_a_row(self):
        metadata = (
            'a:3:{s:5:"width";i:8001;s:6:"height";i:1;s:5:"sizes";a:1:{'
            's:4:"tiny";a:3:{s:4:"file";s:8:"tiny.jpg";'
            's:5:"width";i:10;s:6:"height";i:10;}}}'
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "media.csv"
            out_dir = root / "uploads"
            self._write_csv(
                csv_path,
                [
                    {
                        "file_path": "wide.jpg",
                        "mime_type": "image/jpeg",
                        "metadata_raw": metadata,
                    },
                    {
                        "file_path": "../secret.jpg",
                        "mime_type": "image/jpeg",
                        "metadata_raw": "NULL",
                    },
                    {
                        "file_path": "",
                        "mime_type": "image/jpeg",
                        "metadata_raw": "NULL",
                    },
                ],
            )
            images, empties, skips = generate(csv_path, out_dir)
            self.assertEqual((images, empties, skips), (1, 0, 3))
            self.assertEqual(Image.open(out_dir / "tiny.jpg").size, (10, 10))
            self.assertFalse((out_dir / "wide.jpg").exists())
            self.assertFalse((root / "secret.jpg").exists())

    def test_counts_each_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "media.csv"
            out_dir = root / "uploads"
            row = {
                "file_path": "2018/11/favicon.ico",
                "mime_type": "image/x-icon",
                "metadata_raw": "NULL",
            }
            self._write_csv(csv_path, [row, row])
            images, empties, skips = generate(csv_path, out_dir)
            self.assertEqual((images, empties, skips), (2, 0, 0))
            self.assertEqual(Image.open(out_dir / "2018/11/favicon.ico").size, (32, 32))

    def test_missing_columns_creates_no_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "bad.csv"
            csv_path.write_text("file_path\nfoo\n", encoding="utf-8")
            out_dir = root / "uploads"
            with self.assertRaises(GenerateError) as caught:
                generate(csv_path, out_dir)
            self.assertEqual(str(caught.exception), "必要な列がありません")
            self.assertFalse(out_dir.exists())

    def test_missing_csv_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(GenerateError) as caught:
                generate(root / "missing.csv", root / "uploads")
            self.assertEqual(str(caught.exception), "CSVを開けません")
            self.assertFalse((root / "uploads").exists())


class OptionPathTests(unittest.TestCase):
    def test_collects_uploads_paths_once(self):
        text = (
            's:51:"//localhost/wp-content/uploads/2019/01/slider03.jpg";'
            " /uploads/2019/01/slider03.jpg"
            " http://localhost/wp-content/uploads/2019/06/note.pdf"
            " notuploads/skip.jpg"
        )
        self.assertEqual(
            uploads_paths(text),
            ["2019/01/slider03.jpg", "2019/06/note.pdf"],
        )

    def test_options_add_missing_files_without_replacing_sized_media(self):
        metadata = 'a:2:{s:5:"width";i:10;s:6:"height";i:20;}'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "media.csv"
            options_path = root / "options.csv"
            out_dir = root / "uploads"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["file_path", "mime_type", "metadata_raw"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "file_path": "2018/10/kept.jpg",
                        "mime_type": "image/jpeg",
                        "metadata_raw": metadata,
                    }
                )
            with options_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["option_id", "option_name", "option_value", "autoload"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "option_id": "1",
                        "option_name": "biz_vektor_theme_options",
                        "option_value": (
                            "//localhost/wp-content/uploads/2018/10/kept.jpg "
                            "/uploads/2019/01/slider03.jpg "
                            "uploads/2019/06/note.pdf"
                        ),
                        "autoload": "yes",
                    }
                )
            images, empties, skips = generate(csv_path, out_dir, options_path)
            self.assertEqual((images, empties, skips), (2, 1, 0))
            self.assertEqual(Image.open(out_dir / "2018/10/kept.jpg").size, (10, 20))
            self.assertEqual(Image.open(out_dir / "2019/01/slider03.jpg").size, (32, 32))
            self.assertEqual((out_dir / "2019/06/note.pdf").read_bytes(), b"")

    def test_missing_options_creates_no_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "media.csv"
            csv_path.write_text(
                "file_path,mime_type,metadata_raw\n",
                encoding="utf-8",
            )
            out_dir = root / "uploads"
            with self.assertRaises(GenerateError) as caught:
                generate(csv_path, out_dir, root / "missing-options.csv")
            self.assertEqual(str(caught.exception), "CSVを開けません")
            self.assertFalse(out_dir.exists())


class CommandTests(unittest.TestCase):
    def test_prints_counts_and_returns_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "media.csv"
            out_dir = root / "uploads"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["file_path", "mime_type", "metadata_raw"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "file_path": "2018/11/favicon.ico",
                        "mime_type": "image/x-icon",
                        "metadata_raw": "NULL",
                    }
                )
                writer.writerow(
                    {
                        "file_path": "2019/06/a.pdf",
                        "mime_type": "application/pdf",
                        "metadata_raw": "NULL",
                    }
                )
                writer.writerow(
                    {
                        "file_path": "../secret.jpg",
                        "mime_type": "image/jpeg",
                        "metadata_raw": "NULL",
                    }
                )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["--csv", str(csv_path), "--out", str(out_dir)])
            self.assertEqual(code, 0)
            self.assertEqual(
                stdout.getvalue().splitlines(),
                ["画像: 1", "空ファイル: 1", "スキップ: 1"],
            )

    def test_returns_one_when_output_cannot_be_created(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "media.csv"
            blocked = root / "uploads"
            csv_path.write_text(
                "file_path,mime_type,metadata_raw\n",
                encoding="utf-8",
            )
            blocked.write_text("not a directory", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main(["--csv", str(csv_path), "--out", str(blocked)])
            self.assertEqual(code, 1)
            self.assertEqual(stderr.getvalue().strip(), "出力先を作れません")


if __name__ == "__main__":
    unittest.main()
