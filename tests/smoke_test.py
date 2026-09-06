"""零依赖冒烟测试：python tests/smoke_test.py（无需安装，直接跑）。"""

import json
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fabric_mod_packer import core, versions
from fabric_mod_packer.cli import expand_patterns
from fabric_mod_packer.core import PackError

FAILED = []


def check(name, fn):
    try:
        fn()
    except AssertionError as exc:
        FAILED.append(name)
        print(f"[失败] {name}: {exc}")
    else:
        print(f"[通过] {name}")


def expect_error(fn, fragment):
    try:
        fn()
    except PackError as exc:
        assert fragment in str(exc), f"错误信息里没有找到 {fragment!r}：{exc}"
        return
    raise AssertionError("应当报错却没有报错")


def make_mod(directory, mod_id, version="1.0.0", name=None, environment="*", depends=None,
             extra=None, filename=None):
    """伪造一个最小 Fabric mod jar。"""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    meta = {"schemaVersion": 1, "id": mod_id, "version": version}
    if name is not None:
        meta["name"] = name
    if environment is not None:
        meta["environment"] = environment
    if depends is not None:
        meta["depends"] = depends
    path = directory / (filename or f"{mod_id}.jar")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("fabric.mod.json", json.dumps(meta))
        zf.writestr(f"assets/{mod_id}/icon.png", f"icon-of-{mod_id}")
        for entry, data in (extra or {}).items():
            zf.writestr(entry, data)
    return path


def test_versions():
    m = versions.merge_specs
    assert m(["*", "*"]) == "*"
    assert m(["*", ">=1.0"]) == ">=1.0"
    assert m([">=1.0.0", ">=1.2.0"]) == ">=1.2.0"
    assert m([">=1.0", ">1.0"]) == ">1.0"
    assert m(["<=2.0", "<1.5"]) == "<1.5"
    assert m([">=1.0", "<2.0"]) == ">=1.0 <2.0"
    assert m([">=1.0", "<=1.0"]) == "=1.0"
    assert m(["=1.2.3", ">=1.0", "<2.0"]) == "=1.2.3"
    assert m([["1.0"], "1.0"]) == "1.0"
    assert m(["^1.2", ">=1.0"]) is None
    assert m([["a", "b"], "*"]) is None
    assert m([">=2.0", "<1.0"]) is None
    assert m(["=1.0", "=2.0"]) is None
    assert m([">1.0", "<=1.0"]) is None
    assert m(["1.x", ">=1.0"]) is None


def test_pack_and_unpack():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        alpha = make_mod(tmp / "in", "alpha", version="1.0.0", environment="*",
                         depends={"minecraft": ">=1.20", "fabricloader": ">=0.15", "lib": "*"},
                         extra={"common/shared.class": "a"})
        beta = make_mod(tmp / "in", "beta", name="Beta Mod", version="2.0.0", environment="client",
                        depends={"minecraft": ">=1.20.1", "fabricloader": "*", "lib": ">=1.0"},
                        extra={"common/shared.class": "b"})
        gamma = make_mod(tmp / "in", "gamma", version="0.1", environment=["*"],
                         depends={"minecraft": "*", "custom": "1.0"})
        result = core.pack([alpha, beta, gamma], tmp / "packed.jar")
        meta = result.meta
        assert meta["id"] == "alpha-and-2-more", meta["id"]
        assert meta["version"] == "1.0.0-and-2-more", meta["version"]
        assert meta["name"] == "alpha-and-2-more", meta["name"]
        assert meta["environment"] == "client", meta["environment"]
        assert meta["depends"] == {"minecraft": ">=1.20.1"}, meta["depends"]
        assert [j["file"] for j in meta["jars"]] == [
            "META-INF/jars/alpha.jar", "META-INF/jars/beta.jar", "META-INF/jars/gamma.jar"]
        assert len(result.warnings) == 1 and "common/shared.class" in result.warnings[0]
        # 产物能被本工具重新识别，声明的嵌套 jar 都在包里
        assert core.load_mod(result.output).id == meta["id"]
        with zipfile.ZipFile(result.output) as zf:
            assert zf.testzip() is None
            assert {j["file"] for j in meta["jars"]} <= set(zf.namelist())
        # 解包还原：文件名与字节一致
        out_dir, files = core.unpack(result.output, tmp / "out")
        assert out_dir == tmp / "out"
        assert sorted(f.name for f in files) == ["alpha.jar", "beta.jar", "gamma.jar"]
        for f in files:
            assert f.read_bytes() == (tmp / "in" / f.name).read_bytes(), f


def test_depends_fallback():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        a = make_mod(tmp, "da", depends={"x": "^1.0"})
        b = make_mod(tmp, "db", depends={"x": ">=1.0"})
        result = core.pack([a, b], tmp / "out.jar")
        assert result.meta["depends"] == {"x": "^1.0"}, result.meta["depends"]
        assert any("依赖 x" in w for w in result.warnings), result.warnings


def test_errors():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        client = make_mod(tmp / "e1", "clientmod", environment="client")
        server = make_mod(tmp / "e1", "servermod", environment="server")
        expect_error(lambda: core.pack([client, server]), "environment")
        expect_error(lambda: core.pack([client]), "至少需要 2 个")

        fake = tmp / "fake.jar"
        with zipfile.ZipFile(fake, "w") as zf:
            zf.writestr("readme.txt", "not a mod")
        expect_error(lambda: core.pack([fake, client]), "无法识别")

        garbage = tmp / "garbage.jar"
        garbage.write_bytes(b"not a zip at all")
        expect_error(lambda: core.pack([garbage, client]), "无法识别")

        bad_id = make_mod(tmp / "e1", "BadID")
        expect_error(lambda: core.pack([bad_id, client]), "无法识别")

        dup1 = make_mod(tmp / "e2", "dupe", filename="one.jar")
        dup2 = make_mod(tmp / "e2", "dupe", version="9.9.9", filename="two.jar")
        expect_error(lambda: core.pack([dup1, dup2]), "mod id 重复")

        b1 = make_mod(tmp / "e3" / "x", "moda", filename="same.jar")
        b2 = make_mod(tmp / "e3" / "y", "modb", filename="same.jar")
        expect_error(lambda: core.pack([b1, b2]), "输入文件重名")

        a = make_mod(tmp / "e4", "m1")
        b = make_mod(tmp / "e4", "m2")
        target = tmp / "e4" / "out.jar"
        core.pack([a, b], target)
        expect_error(lambda: core.pack([a, b], target), "已存在")

        expect_error(lambda: core.unpack(client), "可能不是本工具打包")
        expect_error(lambda: expand_patterns([str(tmp / "nope-*.jar")]), "没有文件匹配")


def main():
    check("versions 合并算法", test_versions)
    check("打包/识别/解包全流程", test_pack_and_unpack)
    check("depends 版本要求回退", test_depends_fallback)
    check("各类错误情况", test_errors)
    if FAILED:
        print(f"\n{len(FAILED)} 项失败：{'、'.join(FAILED)}")
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
