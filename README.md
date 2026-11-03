# pinshuai-image-template

轮播图批量模板库。每个模板把一套参考轮播拆成固定文字和可变点，存在 `templates/<模板名>/carousel.json`。脚本给每套、每张画面写一条提示词，场景、机位、景别等可变点的取值由脚本定好并在各套之间轮换，不靠模型自己挑。同时写出可以直接交给 [pinshuai-batch-image](https://github.com/Kangaroochan/pinshuai-batch-image) 的 `jobs.json`，一次出完全部套数。

只写提示词，不出图。没有对应模板、只做一次提示词多样化的，用 [pinshuai-prompt-diversify](https://github.com/Kangaroochan/pinshuai-prompt-diversify)。

## 模板

| 模板 | 内容 |
|---|---|
| `圣诞珍珠画01` | 圣诞老人和小鹿珠钻画挂饰三张轮播：1 开场四宫格 "10/10 Christmas activity"、2 和妈妈一起做 "Ornament Kit 🥹"、3 挂在树上 "that’s our December evenings sorted...🫶🏼"；可配款式池每套轮换 |
| `圣诞珍珠画02` | 6枚一套珠钻画挂饰四张轮播：1 下雪开场、2 整套6个、3 自己做、4 挂上树 + "grab yours today 👇👇"；主打款每套轮换 |
| `圣诞珍珠画03` | 自己做的挂饰挂在圣诞树上六张轮播：1 封面 "pearl ornaments i've made!!!"、2～5 一款一张（最爱款、单款特写、拿在手里、另一款）、6 结尾 "what else should i make!!!!"；产品不固定，每次用上传的产品图建款式池，每套 4 款、款位轮换 |
| `圣诞珍珠画04` | 同一枚挂饰从空白底板到做好的三张手作教程轮播：1 第一步 "How to make pearl ornaments, step by step!"、2 贴珠中 "I'm making the ___ one."、3 做好了 "And voilà! Look how cute!"；产品不固定，每次用上传的产品图建款式池，每套 1 款 |
| `圣诞珍珠画05` | 做珠钻画前后对比两张轮播：1 乱摊子 "The mess:"（一桌材料和工具，没有产品）、2 成品 "The result:" 加这一款的 emoji；产品不固定，每次用上传的产品图建款式池，每套 1 款，emoji 跟着款式变 |
| `圣诞珍珠画06` | 挂饰拿在腿上给镜头看的四张轮播：1 封面 "pearl ornaments I made this weekend >>>"、2 第一枚、3 更顺手、4 最爱 "I'm obsessed."；产品不固定，每次用上传的产品图建款式池，每套 3 款、款位轮换 |
| `圣诞珍珠画07` | 下雪街头开场到挂起来的四张轮播：1 下雪开场 "PARENTSSSS make sure to grab…"（没有产品）、2 贴珠中 "…so relaxing. 🥹✨🎄"、3 做好了 "…cuter than I expected." 加这一款的 emoji、4 挂起来 "Made these myself…" + "see today’s price ⬇️"；产品不固定，每次用上传的产品图建款式池，每套 4 款、款位轮换，emoji 跟着款式变 |
| `圣诞珍珠画08` | 挂在树上、一起做、单枚特写的三张轮播：1 挂在树上 "Made these myself…" + "see today’s price ⬇️"、2 一起做 "make your own ornaments! 🥹🎄"（款位1贴了一半，其余几款做好放在桌上）、3 单枚特写 "Grab one below! 👇"；产品不固定，每次用上传的产品图建款式池，每套 4 款、款位轮换 |
| `圣诞珍珠画09` | 帖子截图开场到挂在树上的三张轮播：1 帖子开场（只有大字的帖子截图 "you get 12 different Christmas Ornaments in one kit??"，没有产品，12 是套装款数，可改）、2 桌上贴珠 "and they’re sooo cute 🤩🤩"（款位1、2做好，其余几款贴了一半）、3 挂在树上 "sorry about the price tho..."（款位1、2）；产品不固定，每次用上传的产品图建款式池，每套 4 款、款位轮换 |

每个模板文件夹里有 `carousel.json`、`参考图/`、`产品图/`、`说明.md`（模板专属规则）和 `lessons.md`（这个模板的出图经验）。

2026-11-02 由 [christmas-pearl-painting-01](https://github.com/Kangaroochan/christmas-pearl-painting-01) 和 [christmas-pearl-painting-02](https://github.com/Kangaroochan/christmas-pearl-painting-02) 合并而成，本仓库接着 01 的提交历史。

## 安装

克隆到 Codex 的技能目录，文件夹名保持 `pinshuai-image-template`：

```powershell
git clone https://github.com/Kangaroochan/pinshuai-image-template.git "$env:USERPROFILE\.codex\skills\pinshuai-image-template"
```

脚本需要 Python 3，只用标准库。

## 使用

在 Codex 里调用 `$pinshuai-image-template`，说用哪个模板、要几套、什么深度（1～10，默认 10）。也可以直接跑：

```powershell
python scripts/make_batch.py --list
python scripts/make_batch.py --template 圣诞珍珠画02 --sets 10 --depth 10 -o D:/素材/圣诞02/圣诞02-1102
python scripts/make_batch.py --template 圣诞珍珠画01 --check
```

`--template` 写模板名，或写复制出来的模板的 `carousel.json` 路径。`-o` 是这次的出图文件夹，建在参考图所在的文件夹里；脚本把提示词、jobs.json 等都写进它的 `other/`，pinshuai-batch-image 出的图放在第一层。

## 产品款式池

用户提供造型一致或相似、图案不同的多款产品时，可在模板配置 `product_pool`（`per_set`、`common_description`、`items`；每款有 `name/ref/description`）。每套从池中不重复抽取、均衡轮换；在用户提供的款式间轮换不算换产品。无款式池时保持原有随机结果。完整字段与占位符见 `SKILL.md` 的"可选产品款式池"。

```powershell
python scripts/make_batch.py --template D:/素材/carousel.json --sets 10 --seed 456084 -o D:/素材/轮换/轮换-1102
python scripts/make_batch.py --template D:/素材/carousel.json --sets 1 --products 雪人 姜饼人 -o D:/素材/指定/指定-1102
```

指定款数等于每套款数时固定，多于时只在点名款中轮换；未知、重复或不足会报错。产品随机流独立，不改变场景、机位等抽取。

一张画面只拍其中一款时，`use_products` 写成款位列表（比如 `[2]`），这张只带这个款位的裁图和描述；款位顺序在各套之间轮换。字幕要写英文款名时每款加 `en`，正文用 `{{款式N英文}}`；字幕里的 emoji 要跟着款式变时每款加 `emoji` 和 `emoji_desc`，正文用 `{{款式Nemoji}}`、`{{款式Nemoji说明}}`。

修复画面问题时用适用于全部选项的通用手部/受力/自然排列规则，不删选项、换安全场景或限制构图；改前改后的差异统计不能降低（2026-11-01 用户要求）。用户明确说不要的画面例外，直接从模板删掉。

## 新增模板

复制结构最接近的模板文件夹，换参考图和产品图，重写 `carousel.json`、`说明.md`，`lessons.md` 从 L001 记起，跑 `--check`，再把新模板写进 `SKILL.md` 的 description。步骤见 `SKILL.md`"新增模板"。

## 文件

- `SKILL.md`：通用规则和流程
- `scripts/make_batch.py`：按模板生成一批；`scripts/sample_prompts.py`：抽取引擎
- `references/lessons.md`：对所有模板都适用的经验索引
- `templates/<模板名>/`：各模板，`lessons.md` 里是作者自己的出图记录

## 许可

MIT
