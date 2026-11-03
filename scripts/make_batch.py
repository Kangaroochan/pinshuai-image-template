#!/usr/bin/env python3
"""
按 templates/<模板名>/carousel.json 给每套轮播的每张画面写一条选项已经定好的提示词。

模型在一条提示词里看到"例如 A、B、C"时几乎总挑同一个，所以这里由脚本替它选：
每个可变点每条只写一个具体取值，取值在各套之间均衡轮换，同一张画面任意两套之间
尽量多处不同。shared 里的可变点（质感、配色、主打款等）每套抽一次，正文引用了它的
画面共用同一个取值，一套轮播看起来是同一个人拍的、讲的是同一件事。

-o 是这次生成的出图文件夹（必须是新的或空的，不覆盖已有内容）。它的第一层留给
pinshuai-batch-image 出的图片，这里写的文件全部放在它的 other/ 子文件夹：
    other/提示词/第01套-1-下雪开场-提示词.txt …… 每条提示词，UTF-8，内容就是要提交的全文
    other/参考图/                                出图要带的产品图（--screenshot 时还有整张截图）
    other/jobs.json                              全部套数，给 render.py 一次跑完，图片会存在出图文件夹第一层
    other/抽取明细.csv                           每条每个可变点取了什么、是不是原图做法
    other/batch.json                             深度、种子、各画面变了几处、实际最小差异

用法：
    python make_batch.py --list                                    # 有哪些模板
    python make_batch.py --template 圣诞珍珠画02 --sets 10          # 默认深度 10
    python make_batch.py --template 圣诞珍珠画01 --sets 20 --depth 7 --seed 42
    python make_batch.py --template 圣诞珍珠画02 --sets 5 --slides 3 4 -o D:/素材/圣诞02/圣诞02-1102
    python make_batch.py --template D:/素材/模板/carousel.json --sets 5   # 复制出来的模板写路径
    python make_batch.py --template 圣诞珍珠画01 --check            # 只检查模板，1～10 档逐档验证
"""
import argparse
import csv
import hashlib
import itertools
import json
import random
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sample_prompts as sp  # noqa: E402  抽取引擎：均衡取值 + 任意两条至少 N 处不同

SKILL_DIR = HERE.parent
SKILL_NAME = SKILL_DIR.name
TEMPLATES_DIR = SKILL_DIR / "templates"
SIZE = re.compile(r"^\d+x\d+$")


class TemplateError(Exception):
    pass


def installed_templates():
    return sorted(p.parent.name for p in TEMPLATES_DIR.glob("*/carousel.json"))


def resolve_template(arg):
    """--template 可以写模板名（templates/ 下的文件夹名），也可以写 carousel.json 或它所在文件夹的路径。"""
    if not arg:
        sys.exit("[错误] 用 --template 写模板名，可选：" + "、".join(installed_templates()))
    named = TEMPLATES_DIR / arg / "carousel.json"
    if named.is_file():
        return named.resolve()
    path = Path(arg)
    if path.is_dir():
        path = path / "carousel.json"
    if path.is_file():
        return path.resolve()
    sys.exit(f"[错误] 找不到模板「{arg}」，可选：" + "、".join(installed_templates()) + "；或者写 carousel.json 的路径")


def list_templates():
    for name in installed_templates():
        tpl = json.loads((TEMPLATES_DIR / name / "carousel.json").read_text(encoding="utf-8-sig"))
        slides = "、".join(s["id"] for s in tpl["slides"])
        pool = tpl.get("product_pool")
        extra = f"；款式池 {len(pool['items'])} 款、每套 {pool['per_set']} 款" if pool else ""
        print(f"{name}：{len(tpl['slides'])} 张（{slides}）{extra}")


def text(t):
    return "\n".join(t) if isinstance(t, list) else t


def options(cfg, depth):
    """按深度返回这个可变点的取值：低于 band 只用原图；band～8 原图是选项之一；9～10 不放原图。"""
    pool = list(cfg["pool"])
    orig = cfg.get("original")
    if orig is None:
        return pool
    if depth < cfg.get("band", 1):
        return [orig]
    return pool if depth >= 9 else pool + [orig]


def sentinel(name):
    return f"\x00{name}\x00"


def shared_used(tpl, slide):
    """这张画面正文里引用了哪些 shared 可变点；没引用的不进这张的明细和差异统计。"""
    used = set(sp.PH.findall(text(slide["template"])))
    return [n for n in tpl.get("shared", {}) if n in used]


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def product_slots(tpl):
    """款位个数：per_set，和各画面 use_products 列表里写到的最大款位，取大的。"""
    pool = tpl.get("product_pool")
    count = pool.get("per_set", 0) if isinstance(pool, dict) else 0
    count = count if is_int(count) and count > 0 else 0
    for s in tpl.get("slides", []):
        picks = s.get("use_products")
        if isinstance(picks, list):
            count = max([count] + [i for i in picks if is_int(i)])
    return count


# 款式的可选字段：占位符后缀 → (字段名, 说明)。正文写 {{款式N<后缀>}}，取本套第 N 个款位这一款的这个字段
ITEM_FIELDS = {"英文": ("en", "英文款名"), "emoji": ("emoji", "emoji字符"), "emoji说明": ("emoji_desc", "emoji图案说明")}


def product_names(tpl):
    if not isinstance(tpl.get("product_pool"), dict):
        return set()
    n = product_slots(tpl)
    return {"产品"} | {f"款式{i}{suffix}" for i in range(1, n + 1) for suffix in ["", *ITEM_FIELDS]}


def map_strings(value, fn):
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, list):
        return [map_strings(v, fn) for v in value]
    if isinstance(value, dict):
        return {k: map_strings(v, fn) for k, v in value.items()}
    return value


def slide_items(slide, row):
    """这张画面出现哪几款：use_products 是 true 时是本套全部款；写成款位列表（比如 [2]）时只取这几个款位。
    款位超过本套款数时从头循环，同一款只算一次。"""
    picks = slide.get("use_products")
    if picks is True:
        return list(row)
    out = []
    for i in picks:
        p = row[(i - 1) % len(row)]
        if p not in out:
            out.append(p)
    return out


def product_values(tpl, row, shown):
    """{{产品}} = 共用描述 + 这张画面出现的各款描述；{{款式N}} = 本套第 N 个款位的款名，
    {{款式N英文}}、{{款式Nemoji}}、{{款式Nemoji说明}} = 这一款的 en、emoji、emoji_desc。"""
    vals = {"产品": tpl["product_pool"]["common_description"] + "".join(p["description"] for p in shown)}
    for i in range(1, product_slots(tpl) + 1):
        p = row[(i - 1) % len(row)]
        vals[f"款式{i}"] = p["name"]
        for suffix, (key, _) in ITEM_FIELDS.items():
            if key in p:
                vals[f"款式{i}{suffix}"] = p[key]
    return vals


def deal_products(tpl, sets, seed, wanted=None):
    """独立随机流；每步优先最低使用次数，保持全池次数差≤1；组合尽量不在整批里重复。"""
    pool = tpl.get("product_pool")
    if pool is None:
        if wanted:
            raise TemplateError("--products 需要模板定义 product_pool")
        return None
    items, k = pool["items"], pool["per_set"]
    names = {p["name"] for p in items}
    if wanted:
        if len(set(wanted)) != len(wanted):
            raise TemplateError("--products 不能重复点名同一款")
        if set(wanted) - names:
            raise TemplateError(f"--products 款名不在池里：{sorted(set(wanted) - names)}")
        if len(wanted) < k:
            raise TemplateError(f"--products 至少指定 {k} 款")
        by_name = {p["name"]: p for p in items}
        eligible = [by_name[n] for n in wanted]
    else:
        eligible = items
    rng = random.Random(f"{seed}|products")
    # 同一组合在整批里重复出现也会让批次显得重复：多发几轮，取重复组合最少的一轮
    best, best_repeats, last_error = None, None, None
    for _ in range(200 if len(eligible) > k else 1):
        try:
            rows = deal_product_rows(eligible, k, sets, rng)
        except TemplateError as e:
            last_error = e
            continue
        repeats = len(rows) - len({frozenset(p["name"] for p in row) for row in rows})
        if best is None or repeats < best_repeats:
            best, best_repeats = rows, repeats
        if repeats == 0:
            break
    if best is None:
        raise last_error
    # 有画面只拍某个款位时，款位决定这一款是不是主角，套内顺序也要轮换；用单独的随机流，不改变抽到哪几款
    ordered = any(isinstance(s.get("use_products"), list) for s in tpl["slides"])
    if ordered:
        best = order_rows(best, random.Random(f"{seed}|product-order"))
    out = {"rows": best, "source": "用户指定" if wanted else "每套轮换", "requested": wanted,
           "repeated_combos": best_repeats,
           "counts": {p["name"]: sum(p in row for row in best) for p in items}}
    if ordered:
        out["slot_counts"] = [{p["name"]: sum(row[i]["name"] == p["name"] for row in best) for p in items}
                              for i in range(k)]
    return out


def order_rows(rows, rng):
    """逐套排款位：每个款位优先放在这个款位出现次数最少的款，次数相同时随机。"""
    seen = [{} for _ in range(len(rows[0]))]
    out = []
    for row in rows:
        left, ordered = list(row), []
        for i, counts in enumerate(seen):
            low = min(counts.get(p["name"], 0) for p in left)
            p = rng.choice([p for p in left if counts.get(p["name"], 0) == low])
            left.remove(p)
            ordered.append(p)
            counts[p["name"]] = counts.get(p["name"], 0) + 1
        out.append(ordered)
    return out


def deal_product_rows(eligible, k, sets, rng):
    """一轮发牌：每步优先最低使用次数，保持次数差≤1，优先没用过的组合，相邻两套不同组。"""
    counts, rows, previous, used = [0] * len(eligible), [], set(), set()
    for _ in range(sets):
        low = min(counts)
        least = [i for i, c in enumerate(counts) if c == low]
        higher = [i for i, c in enumerate(counts) if c > low]
        take_low = min(k, len(least))
        chosen = set(rng.sample(least, take_low) + rng.sample(higher, k - take_low))
        for _ in range(50 if len(eligible) > k else 0):
            if frozenset(chosen) not in used:
                break
            chosen = set(rng.sample(least, take_low) + rng.sample(higher, k - take_low))
        if chosen == previous and len(eligible) > k:
            swaps = [(a, b) for a in sorted(chosen) for b in range(len(eligible))
                     if b not in chosen and counts[a] == counts[b]]
            if not swaps:
                raise TemplateError("无法在均衡次数的同时避免相邻同组")
            a, b = rng.choice(swaps)
            chosen.remove(a); chosen.add(b)
        rows.append([eligible[i] for i in sorted(chosen)])
        for i in chosen:
            counts[i] += 1
        previous = chosen
        used.add(frozenset(chosen))
    return rows


def build(tpl, slide, depth):
    """把一张画面的模板按深度转成 sample_prompts 的规格。"""
    body = text(slide["template"])
    for name, value in tpl.get("fixed", {}).items():
        if name in product_names(tpl):
            continue
        body = body.replace("{{" + name + "}}", value)
    for name in tpl.get("shared", {}):
        body = body.replace("{{" + name + "}}", sentinel(name))
    fixed, originals = {}, {}
    raw = {"template": body, "slots": {}, "groups": []}

    for name, cfg in slide.get("slots", {}).items():
        originals[name] = cfg.get("original")
        opts = options(cfg, depth)
        if len(opts) == 1:
            fixed[name] = opts[0]
        else:
            raw["slots"][name] = {"pool": opts}

    for g in slide.get("groups", []):
        positions, open_ = g["positions"], depth >= g.get("band", 1)
        role_orig = {r: c["original"] for r, c in g["roles"].items()}
        originals.update(role_orig)
        pslots = {}
        for sname, cfg in g.get("position_slots", {}).items():
            per_pos = cfg["original"]
            for p in positions:
                originals[f"{p}.{sname}"] = per_pos[p]
            if depth < cfg.get("band", 1):
                fixed.update({f"{p}.{sname}": per_pos[p] for p in positions})
            else:
                pool = list(cfg["pool"])
                if depth < 9:
                    pool += [v for v in per_pos.values() if v not in pool]
                pslots[sname] = {"pool": pool, "distinct": cfg.get("distinct", True)}
        if not open_ and not pslots:
            fixed.update({p: role_orig[r] for p, r in zip(positions, g["roles"])})
            continue
        roles = {r: {"pool": options({**c, "band": g.get("band", 1)}, depth) if open_ else [c["original"]]}
                 for r, c in g["roles"].items()}
        raw["groups"].append({"positions": positions, "roles": roles,
                              "shuffle": open_ and g.get("shuffle", True), "position_slots": pslots,
                              "avoid_identity": depth >= 9})  # 9～10 档原图的宫格排法不进选项

    for name, value in fixed.items():
        raw["template"] = raw["template"].replace("{{" + name + "}}", value)
    raw["conflicts"] = prune_conflicts(slide.get("conflicts", []), fixed, raw)
    if tpl.get("product_pool") is not None:
        def defer(s):
            return sp.PH.sub(lambda m: sentinel(m.group(1).strip()) if m.group(1).strip() in product_names(tpl) else m.group(0), s)
        raw = map_strings(raw, defer)
        fixed = map_strings(fixed, defer)
        originals = map_strings(originals, defer)
    return raw, fixed, originals


def prune_conflicts(rules, fixed, raw):
    """固定下来的维度已经不在规格里：按固定值判断规则还会不会成立，再删掉这部分条件；
    还在变的维度只保留当前选项里有的取值。"""
    valid = {n: set(c["pool"]) for n, c in raw["slots"].items()}
    for g in raw["groups"]:
        content = set().union(*(set(r["pool"]) for r in g["roles"].values()))
        for p in g["positions"]:
            valid[p] = content
            for s, c in g["position_slots"].items():
                valid[f"{p}.{s}"] = set(c["pool"])
    out = []
    for rule in rules:
        kept, alive = {}, True
        for dim, vals in rule.items():
            vals = [vals] if isinstance(vals, str) else list(vals)
            if dim in fixed:
                alive = fixed[dim] in vals
            elif dim in valid:
                vals = [v for v in vals if v in valid[dim]]
                alive = bool(vals)
                kept[dim] = vals
            else:
                raise TemplateError(f"conflicts 引用了不存在的维度「{dim}」")
            if not alive:
                break
        if alive and kept:
            out.append(kept)
        elif alive:
            raise TemplateError(f"conflicts 规则在这个深度下必然成立，模板自相矛盾：{rule}")
    return out


def varied(spec):
    """一条提示词里真正会变的维度数（只有一个取值的维度不算）。"""
    n = sum(len(c["pool"]) > 1 for c in spec.slots.values())
    for g in spec.groups:
        n += sum(len(c["pool"]) > 1 for c in g["roles"].values())
        n += sum(len(c["pool"]) > 1 for c in g["position_slots"].values()) * len(g["roles"])
        n += 1 if g["shuffle"] else 0
    return n


def fixed_names(spec, fixed):
    """这个深度下固定不变的可变点：模板里直接替换掉的，加上规格里只剩一个取值的。"""
    names = list(fixed)
    names += [n for n, c in spec.slots.items() if len(c["pool"]) == 1]
    for g in spec.groups:
        names += [r for r, c in g["roles"].items() if len(c["pool"]) == 1]
    return names


def make_spec(raw, sets, min_diff):
    try:
        spec = sp.Spec(raw, sets, min_diff)
        sp.check_template(spec)
        sp.check_conflicts(spec)
    except SystemExit as e:  # sample_prompts 用 sys.exit 报错
        raise TemplateError(str(e)) from None
    return spec


def sample(raw, sets, target, seed, label, seconds):
    """从 target 处差异开始往下试，直到能凑够 sets 条。返回 (spec, rows, 用的差异要求)。"""
    probe = make_spec(raw, sets, 0)
    top = min(target, varied(probe))
    for md in range(top, -1, -1):
        spec = make_spec(raw, sets, md)
        rng = random.Random(f"{seed}|{label}|{md}")
        try:
            _, rows, *_ = sp.generate(spec, rng, sets, 3000, seconds)
        except SystemExit as e:
            raise TemplateError(str(e)) from None
        if len(rows) >= sets:
            return spec, rows, md
    raise TemplateError(f"{label}：选项之间的冲突太多，连 {sets} 条都凑不出来")


def deal_shared(tpl, sets, depth, rng):
    """shared 里每个可变点每套一个取值，均衡轮换（一轮用完全部选项才重复）。"""
    out = {}
    for name, cfg in tpl.get("shared", {}).items():
        opts = options(cfg, depth)
        seq = []
        while len(seq) < sets:
            batch = opts[:]
            rng.shuffle(batch)
            seq += batch
        out[name] = seq[:sets]
    return out


def render(spec, row, shared_values, products=None):
    s = sp.render(spec, row["values"])
    for name, value in shared_values.items():
        s = s.replace(sentinel(name), value)
    if products:
        s = sp.PH.sub(lambda m: products.get(m.group(1).strip(), m.group(0)), s)
        for name, value in products.items():
            s = s.replace(sentinel(name), value)
    if "\x00" in s or sp.PH.search(s):
        raise TemplateError("渲染后还有没替换掉的占位符")
    return s


def min_pairwise(vectors):
    if len(vectors) < 2:
        return None
    return min(sum(a != b for a, b in zip(u, v)) for u, v in itertools.combinations(vectors, 2))


def validate(tpl, tpl_dir):
    """模板本身的检查，不看深度。"""
    errors, ids = [], set()
    shared, fixed_names = set(tpl.get("shared", {})), set(tpl.get("fixed", {}))
    products = product_names(tpl)
    pool = tpl.get("product_pool")
    if pool is not None:
        if not isinstance(pool, dict):
            return ["product_pool 必须是对象"]
        items, k = pool.get("items", []), pool.get("per_set")
        if not isinstance(items, list) or not items or any(not isinstance(p, dict) for p in items):
            return ["product_pool.items 必须是非空款式对象列表"]
        if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= len(items):
            return ["product_pool.per_set 必须是正整数，且不超过池子大小"]
        if not isinstance(pool.get("common_description"), str) or not pool["common_description"].strip():
            errors.append("product_pool.common_description 不能为空")
        names, files = set(), set()
        body = json.dumps(tpl["slides"], ensure_ascii=False)
        need = {suffix: sorted({m.strip() for m in sp.PH.findall(body) if re.fullmatch(rf"款式\d+{suffix}", m.strip())})
                for suffix in ITEM_FIELDS}
        for p in items:
            name, ref, desc = p.get("name"), p.get("ref"), p.get("description")
            for suffix, (key, label) in ITEM_FIELDS.items():
                if key in p and (not isinstance(p[key], str) or not p[key].strip() or sp.PH.search(p[key])):
                    errors.append(f"款式 {name} 的 {key} 要写成非空的{label}，不含占位符")
                elif need[suffix] and key not in p:
                    errors.append(f"款式 {name} 缺少 {key}：画面里用到了 {'、'.join(need[suffix])}，每款都要写{label}")
            if not isinstance(name, str) or not name.strip() or name in names:
                errors.append(f"款名为空或重复：{name}")
            names.add(name)
            if not isinstance(ref, str) or not ref or not (tpl_dir / ref).is_file():
                errors.append(f"款式 {name} 找不到裁图：{ref}")
            elif Path(ref).name in files:
                errors.append(f"裁图文件名重复，复制时会冲突：{ref}")
            else:
                files.add(Path(ref).name)
            if not isinstance(desc, str) or not desc.strip():
                errors.append(f"款式 {name} 描述不能为空")
            if any(sp.PH.search(v) for v in [name or "", ref or "", desc or ""]):
                errors.append(f"款式 {name} 的名字、裁图和描述不能包含占位符")
        if sp.PH.search(pool.get("common_description", "")):
            errors.append("common_description 不能包含占位符")

    def check_slot(where, name, cfg):
        pool = cfg.get("pool", [])
        if not pool or len(set(pool)) != len(pool):
            errors.append(f"{where}「{name}」的 pool 为空或有重复")
        if cfg.get("band", 1) > 1 and cfg.get("original") is None:
            errors.append(f"{where}「{name}」band>1 但没有 original，低档位没有原图做法可固定")
        if cfg.get("original") in pool:
            errors.append(f"{where}「{name}」的 original 不要再写进 pool，脚本会按深度自己加")

    for name, cfg in tpl.get("shared", {}).items():
        check_slot("shared", name, cfg)
    unused = shared - {n for slide in tpl["slides"] for n in shared_used(tpl, slide)}
    if unused:
        errors.append(f"shared 里这些可变点没有任何画面正文用到：{sorted(unused)}")
    for slide in tpl["slides"]:
        sid = slide.get("id", "?")
        if sid in ids:
            errors.append(f"画面 id 重复：{sid}")
        ids.add(sid)
        if not SIZE.match(slide.get("size", "")):
            errors.append(f"{sid}：size 要写成 1024x1536 这样")
        for r in slide.get("refs", []) + ([slide["screenshot"]] if slide.get("screenshot") else []):
            if not (tpl_dir / r).is_file():
                errors.append(f"{sid}：找不到参考图 {r}")
        expected = set(slide.get("slots", {}))
        for name, cfg in slide.get("slots", {}).items():
            check_slot(sid, name, cfg)
        for g in slide.get("groups", []):
            positions = g["positions"]
            if len(g["roles"]) != len(positions):
                errors.append(f"{sid}：roles 数量要等于 positions 数量")
            for r, c in g["roles"].items():
                check_slot(sid, r, {**c, "band": g.get("band", 1)})
            for s, c in g.get("position_slots", {}).items():
                if set(c.get("original", {})) != set(positions):
                    errors.append(f"{sid}：位置插槽「{s}」的 original 要给每个位置各写一个")
                if c.get("distinct", True) and len(c["pool"]) < len(positions):
                    errors.append(f"{sid}：位置插槽「{s}」选项不够分给 {len(positions)} 个位置")
                expected |= {f"{p}.{s}" for p in positions}
            expected |= set(positions)
        clash = (expected | {r for g in slide.get("groups", []) for r in g["roles"]}) & (shared | fixed_names)
        if clash:
            errors.append(f"{sid}：这些名字和 shared/fixed 重名：{sorted(clash)}")
        if pool is None and isinstance(slide.get("use_products"), list):
            errors.append(f"{sid}：use_products 写了款位列表，但模板没有 product_pool")
        used = set(sp.PH.findall(text(slide["template"])))
        if used - expected - shared - fixed_names - products:
            errors.append(f"{sid}：模板里有没定义的占位符 {sorted(used - expected - shared - fixed_names - products)}")
        if pool is not None:
            all_used = set()
            map_strings(slide, lambda s: all_used.update(sp.PH.findall(s)) or s)
            unknown = all_used - expected - shared - fixed_names - products
            if unknown:
                errors.append(f"{sid}：正文或选项里有没定义的占位符 {sorted(unknown)}")
            picks = slide.get("use_products")
            if picks is not None and not isinstance(picks, bool) and not (
                    isinstance(picks, list) and picks and all(is_int(i) and i >= 1 for i in picks)
                    and len(set(picks)) == len(picks)):
                errors.append(f"{sid}：use_products 写 true，或写不重复的款位编号列表，比如 [2]")
            if slide.get("use_products") and slide.get("refs"):
                errors.append(f"{sid}：use_products 画面不可另带固定 refs；只带本套抽到的裁图")
            if not slide.get("use_products") and all_used & products:
                errors.append(f"{sid}：引用款式占位符时必须设置 use_products: true")
        if expected - used:
            errors.append(f"{sid}：这些可变点定义了但模板里没用到 {sorted(expected - used)}")
    return errors


def pick_slides(tpl, wanted):
    if not wanted:
        return tpl["slides"]
    out = []
    for w in wanted:
        hit = [s for s in tpl["slides"] if s["id"] == w or s["id"].startswith(w + "-")]
        if not hit:
            sys.exit(f"[错误] 模板里没有画面「{w}」，可选：{', '.join(s['id'] for s in tpl['slides'])}")
        out += [s for s in hit if s not in out]
    return out


def default_min_diff(n):
    return max(1, (n + 1) // 2)


def run_check(tpl, tpl_dir):
    errors = validate(tpl, tpl_dir)
    if errors:
        sys.exit("[错误] 模板有问题：\n  " + "\n  ".join(errors))
    print("深度  " + "  ".join(f"{s['id']}(变几处)" for s in tpl["slides"]))
    for depth in range(1, 11):
        cells = []
        for slide in tpl["slides"]:
            try:
                raw, _, _ = build(tpl, slide, depth)
                cells.append(str(varied(make_spec(raw, None, 0))))
            except TemplateError as e:
                sys.exit(f"[错误] 深度 {depth}，{slide['id']}：{e}")
        print(f"{depth:>4}  " + "  ".join(f"{c:>{len(s['id']) + 6}}" for c, s in zip(cells, tpl["slides"])))
    names = list(tpl.get("shared", {}))
    shared = sum(len(options(c, 10)) > 1 for c in tpl.get("shared", {}).values())
    print(f"模板检查通过。另有 {len(names)} 个每套共用的可变点（{'、'.join(names)}），10 档时 {shared} 个会变。")
    for slide in tpl["slides"]:
        print(f"  {slide['id']} 用到的共用可变点：{'、'.join(shared_used(tpl, slide)) or '无'}")


def csv_rows(set_no, slide_id, prompt_name, spec, row, fixed, originals, groups_cfg):
    out = []

    def add(dim, value, orig, how):
        out.append([set_no, slide_id, prompt_name, dim, value, how,
                    "" if orig is None else ("是" if value == orig else "否")])

    for n in spec.slots:
        add(n, row["values"][n], originals.get(n), "抽取")
    for gi, g in enumerate(spec.groups):
        cfg = next(c for c in groups_cfg if c["positions"] == g["positions"])
        home = dict(zip(cfg["roles"], g["positions"]))  # 每类内容在原图里的格子
        order = [p.split("=")[1] for p in row["dims"][f"排列{gi + 1}"].split("；")] if g["shuffle"] else list(g["roles"])
        if g["shuffle"]:
            natural = "；".join(f"{p}={r}" for r, p in home.items())
            add("宫格排列", row["dims"][f"排列{gi + 1}"], natural, "抽取")
        for p, r in zip(g["positions"], order):
            how = "抽取" if len(g["roles"][r]["pool"]) > 1 else "固定"
            add(f"{p}（{r}）", row["values"][p], originals.get(r), how)
            for s in g["position_slots"]:  # 原图做法按这类内容在原图里的拍法比，不按格子比
                add(f"{p}.{s}", row["values"][f"{p}.{s}"], originals.get(f"{home[r]}.{s}"), "抽取")
    for n, v in fixed.items():
        add(n, v, originals.get(n, v), "固定")
    return out


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="按轮播模板批量写提示词")
    ap.add_argument("--sets", type=int, help="出几套轮播（每套每张画面一条提示词）")
    ap.add_argument("--depth", type=int, default=10, help="重构深度 1～10，默认 10")
    ap.add_argument("--slides", nargs="+", help="只要某几张画面，写 id 或编号，比如 3 4")
    ap.add_argument("--seed", type=int, help="随机种子，同模板同种子结果可复现；不写就随机")
    ap.add_argument("--min-diff", type=int, help="同一张画面任意两套至少几处不同；默认是会变的可变点数的一半")
    ap.add_argument("--screenshot", action="store_true", help="另带整张截图做构图参考，只允许 1～6 档")
    ap.add_argument("--model", help="写进 jobs 文件的模型名，不写就用 render.py 的默认")
    ap.add_argument("--products", nargs="+", help="在款式池中点名：刚好每套款数则固定，多于则仅在点名款之间均衡轮换")
    ap.add_argument("--template", help="模板名（--list 列出），或 carousel.json 的路径")
    ap.add_argument("--list", action="store_true", help="列出 templates/ 下的模板")
    ap.add_argument("-o", "--out", help="出图文件夹（建在参考图所在文件夹里），文件写进它的 other/；"
                                         "默认 <当前目录>/<模板名>-<时间>-深度N-M套")
    ap.add_argument("--max-seconds", type=float, default=30, help="每张画面每个差异要求最多试多久")
    ap.add_argument("--check", action="store_true", help="只检查模板，不写文件")
    args = ap.parse_args()

    if args.list:
        return list_templates()
    tpl_path = resolve_template(args.template)
    tpl_bytes = tpl_path.read_bytes()
    tpl = json.loads(tpl_bytes.decode("utf-8-sig"))
    tpl_dir = tpl_path.parent
    if args.check:
        if args.products:
            errors = validate(tpl, tpl_dir)
            if errors:
                sys.exit("[错误] " + "；".join(errors))
            try:
                deal_products(tpl, 1, args.seed or 0, args.products)
            except TemplateError as e:
                sys.exit(f"[错误] {e}")
        return run_check(tpl, tpl_dir)
    errors = validate(tpl, tpl_dir)
    if errors:
        sys.exit("[错误] 模板有问题，先跑 --check：\n  " + "\n  ".join(errors))
    if not args.sets or args.sets < 1:
        sys.exit("[错误] 用 --sets 写要出几套")
    if not 1 <= args.depth <= 10:
        sys.exit("[错误] --depth 要是 1～10 的整数")
    if args.screenshot and args.depth > 6:
        sys.exit("[错误] 7～10 档不带整张截图：实测带截图时 4 张里 3 张沿用了原图布局")

    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(10 ** 6)
    slides = pick_slides(tpl, args.slides)
    try:
        products = deal_products(tpl, args.sets, seed, args.products)
    except TemplateError as e:
        sys.exit(f"[错误] {e}")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(args.out).resolve() if args.out else \
        Path.cwd() / f"{tpl.get('name', 'batch')}-{stamp}-深度{args.depth}-{args.sets}套"
    if out.exists() and any(out.iterdir()):
        sys.exit(f"[错误] {out} 已经有内容，换一个 -o，不覆盖")
    gen_dir, out = out, out / "other"  # 出图文件夹第一层只放图片，这次生成的其他文件都进 other/

    shared_rng = random.Random(f"{seed}|shared")
    shared = deal_shared(tpl, args.sets, args.depth, shared_rng)
    width = max(2, len(str(args.sets)))
    (out / "提示词").mkdir(parents=True, exist_ok=True)
    (out / "参考图").mkdir(exist_ok=True)

    results, table, jobs = [], [], []
    for slide in slides:
        try:
            raw, fixed, originals = build(tpl, slide, args.depth)
            n_var = varied(make_spec(raw, None, 0))
            target = args.min_diff if args.min_diff is not None else default_min_diff(n_var)
            spec, rows, used_md = sample(raw, args.sets, target, seed, slide["id"], args.max_seconds)
        except TemplateError as e:
            sys.exit(f"[错误] {slide['id']}：{e}")
        refs = list(slide.get("refs", [])) + ([slide["screenshot"]] if args.screenshot and slide.get("screenshot") else [])
        ref_paths = []
        for r in refs:
            dst = out / "参考图" / Path(r).name
            if not dst.exists():
                shutil.copy2(tpl_dir / r, dst)
            ref_paths.append(f"参考图/{dst.name}")
        dims = spec.dim_names()
        used_shared = shared_used(tpl, slide)
        vectors = []
        for i, row in enumerate(rows[:args.sets]):
            set_label = f"第{i + 1:0{width}d}套"
            sv = {n: shared[n][i] for n in used_shared}
            shown = slide_items(slide, products["rows"][i]) if products and slide.get("use_products") else None
            pv = product_values(tpl, products["rows"][i], shown) if shown else None
            job_refs = ref_paths
            if pv:
                job_refs = []
                for p in shown:
                    dst = out / "参考图" / Path(p["ref"]).name
                    if not dst.exists():
                        shutil.copy2(tpl_dir / p["ref"], dst)
                    job_refs.append(f"参考图/{dst.name}")
            prompt = render(spec, row, sv, pv)
            name = f"{set_label}-{slide['id']}"
            fname = f"{name}-提示词.txt"
            (out / "提示词" / fname).write_bytes(prompt.encode("utf-8"))
            job = {"name": name, "prompt": f"提示词/{fname}", "refs": job_refs, "count": 1, "size": slide["size"]}
            jobs.append(job)
            table += csv_rows(set_label, slide["id"], fname, spec, row, fixed, originals, slide.get("groups", []))
            if pv:
                def resolved(value):
                    value = sp.PH.sub(lambda m: pv.get(m.group(1).strip(), m.group(0)), value)
                    for key, replacement in pv.items():
                        value = value.replace(sentinel(key), replacement)
                    return value
                table = [[*r[:4], resolved(r[4]), *r[5:]]
                         if r[0] == set_label and r[1] == slide["id"] else r for r in table]
                table.append([set_label, slide["id"], fname, "产品款式", "、".join(p["name"] for p in shown), products["source"], ""])
            for n in used_shared:
                orig = tpl["shared"][n].get("original")
                table.append([set_label, slide["id"], fname, n, sv[n], "每套共用",
                              "" if orig is None else ("是" if sv[n] == orig else "否")])
            vectors.append([row["dims"][d] for d in dims] + [sv[n] for n in used_shared])
        n_shared = sum(len(set(shared[n])) > 1 for n in used_shared)
        results.append({"id": slide["id"], "varied": n_var, "fixed": fixed_names(spec, fixed), "min_diff_target": target,
                        "min_diff_used": used_md, "min_diff_actual": min_pairwise(vectors),
                        "shared": used_shared, "varied_with_shared": n_var + n_shared, "refs": ref_paths,
                        **({"refs_per_job": len(slide_items(slide, products["rows"][0]))}
                           if products and slide.get("use_products") else {})})

    def write_jobs(fname, jobs):
        # 出图 skill 同步经验时，按 prompt_skill + template 找到 templates/<template>/lessons.md
        spec = {"prompt_skill": SKILL_NAME, "template": tpl.get("name", tpl_dir.name)}
        if args.model:
            spec["model"] = args.model
        spec["jobs"] = jobs
        (out / fname).write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    jobs.sort(key=lambda j: j["name"])
    write_jobs("jobs.json", jobs)
    with open(out / "抽取明细.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["套", "画面", "提示词文件", "可变点", "取值", "来源", "是原图做法"])
        w.writerows(table)
    meta = {"template": str(tpl_path), "template_sha256": hashlib.sha256(tpl_bytes).hexdigest(),
            "created": datetime.now().isoformat(timespec="seconds"), "depth": args.depth, "sets": args.sets,
            "seed": seed, "screenshot": args.screenshot, "slides": results,
            "shared_by_set": shared, "jobs": "jobs.json"}
    if products:
        meta.update({"product_pool": tpl["product_pool"], "products_requested": args.products,
                     "product_counts": products["counts"], "repeated_combos": products["repeated_combos"],
                     "products_by_set": [{"set": f"第{i + 1:0{width}d}套", "source": products["source"],
                                          "products": [p["name"] for p in row],
                                          "refs": [f"参考图/{Path(p['ref']).name}" for p in row]}
                                         for i, row in enumerate(products["rows"])]})
        if "slot_counts" in products:
            meta["product_slot_counts"] = products["slot_counts"]
    (out / "batch.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"深度 {args.depth}，{args.sets} 套，种子 {seed} → {gen_dir}（文件在 other/，图片会出在这一层）")
    for r in results:
        note = "" if r["min_diff_used"] == r["min_diff_target"] else \
            f"（要求 {r['min_diff_target']} 处凑不够，降到 {r['min_diff_used']} 处）"
        actual = "-" if r["min_diff_actual"] is None else r["min_diff_actual"]
        print(f"  {r['id']}：会变 {r['varied']} 处 + 每套共用 {len(r['shared'])} 处，固定 {len(r['fixed'])} 处；"
              f"任意两套至少 {actual} 处不同{note}；带图 {r.get('refs_per_job', len(r['refs']))} 张")
    for n, cfg in tpl.get("shared", {}).items():
        # 没有原图做法的共用项是纯轮换（比如主打款），报一下各取值出现几次
        if cfg.get("original") is None and any(n in shared_used(tpl, s) for s in slides):
            print(f"  {n}出现次数：" + "，".join(f"{v} {shared[n].count(v)}次" for v in cfg["pool"]))
    if products:
        print("  款式出现次数：" + "，".join(f"{n} {c}次" for n, c in products["counts"].items()))
        print(f"  重复出现的款式组合：{products['repeated_combos']} 套")
        for i, counts in enumerate(products.get("slot_counts", []), 1):
            print(f"  款位{i}出现次数：" + "，".join(f"{n} {c}次" for n, c in counts.items()))
    print(f"  提示词 {len(jobs)} 条，jobs.json {len(jobs)} 张")
    if args.screenshot:
        print("[提醒] 带了整张截图：圣诞珍珠画01 的 L008 记录过截图里的界面（搜索栏、轮播圆点或箭头）会被画进图里，出图后要检查")


if __name__ == "__main__":
    main()
