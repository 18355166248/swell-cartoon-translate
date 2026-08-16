# swell-cartoon-translate

漫画汉化流水线。检测 → OCR → 翻译 → 擦除 → 排版。

针对 **GTX 1650 SUPER (4GB) + 16GB 内存** 这类配置设计，目标是解决 BallonsTranslator
在该硬件上「卡顿」与「翻译效果差」两个问题。

## 一键初始化

换电脑后跑这一个脚本：装依赖、下模型、自检。

```bash
powershell -File scripts\setup.ps1
```

Linux / macOS：

```bash
./scripts/setup.sh
```

**幂等设计，重复跑是安全的**——已装好的会跳过。所以中途断网或下载失败，直接重跑
即可续传，不用先清理。只想搭环境不下 4.7GB 模型：加 `-SkipModels` / `--skip-models`。

需要 **Python 3.11+**（`tomllib` 是 3.11 才进标准库的）。

## 跑起来

```bash
cd backend
python -m ctt.cli translate ..\assets -o ..\out          # 完整流程
python -m ctt.cli detect ..\assets\en4.jpg --visualise   # 只跑检测，验证环境
python -m ctt.cli config                                  # 看当前生效配置
```

密钥只从环境变量读，不进配置文件：

```bash
set DEEPL_API_KEY=xxxxx:fx
set CTT_LLM_URL=http://localhost:11434/v1
```

## 仓库里没有什么

`assets/`、`result/`、`out/`（漫画源图与成品）和 `backend/models/`（4.7GB 权重）
都在 `.gitignore` 里。前者是版权内容且这是公开仓库，后者超 GitHub 单文件 100MB
限制。模型由 `setup.ps1` 重新下载，图片放回 `assets/` 即可——代码不依赖具体图片。

## 批量与过滤

打开 `[input].recursive`（或界面上的开关），指向系列文件夹就能一次翻完所有话。

跑之前**先点「预览选中」**——递归之后选中了什么不是肉眼能预判的，而猜错要花几小时。
预览会列出纳入数量、按目录分布、预计耗时，以及每个被跳过文件的原因：

```
总计 291   纳入 289   跳过 2      预计 173 分钟
  289  G:\Download\Cartoon\satisfying-needs\chapter-1-4
  page-1-13-satisfying-needs-2_zh.jpg   [疑似输出目录]
  page-1-13-satisfying-needs-1.png      [文件过小 22KB]
```

四道过滤，都可在 `[input]` 里调：

| 项 | 作用 |
|---|---|
| `min_bytes` | 小于此体积跳过。缩略图、横幅远小于正文页 |
| `min_side` | 宽或高任一过小就跳过 |
| `max_aspect` | 宽/高 超过此值判为横幅 |
| `skip_output_dirs` | 跳过 `_zh` / `out` / `translated` / `汉化` 这类目录 |

两个设计上刻意的地方：

**`min_side` 分别限制两边，不是限制面积。** 长条 webtoon 窄而极高（实测 720×13859），
按面积算会误杀。

**`max_aspect` 只限制横向。** 竖条漫画常达 20:1，正是要保留的；横向 4:1 才是横幅
和标题卡。

**输出目录必须排除**，否则递归会把上次的成品当原图再翻一遍，得到「译文的译文」。
输出目录本身无条件排除，`skip_output_dirs` 额外按名字排除常见命名。

## 配置

所有设置集中在项目根的 [ctt.toml](ctt.toml)，每一项都有注释说明。
优先级：**命令行参数 > 环境变量 > ctt.toml > 代码内置默认值**。

查看当前实际生效的完整配置（也可以带上参数预演，不必真的跑一遍）：

```bash
python -m ctt.cli config
python -m ctt.cli config --target zh-Hant --translators llamacpp nllb
```

配置文件按目录向上查找，所以在子目录里跑命令也能找到。改配置**不需要动代码**——
在此之前，字号、切片高度、线程数这些散落在 8 个模块的常量里。

**密钥不进配置文件。** `ctt.toml` 是要提交的，密钥只从环境变量读：
`DEEPL_API_KEY`、`CTT_LLM_KEY`。

写错的键会警告而不是静默忽略：

```
warning: ctt.toml: unknown setting 'tarrget_lang'
```

## 架构要点

流水线分 7 个阶段，`backend/ctt/pipeline.py` 编排。三个决定性设计：

**1. 长条先切片，任何模型都看不到整图。**
`slicing.py` 用逐行「含墨量」找分镜间隙，切成 ≤2500px 的块。判据是：整行像素与该行
中位数的偏离比例低于 0.2% ⇒ 该行无文字 ⇒ 在此切割不可能切断气泡。
实测 `en4.jpg` (720×13859) 峰值模型输入从 9.98MP 降到 1.80MP（5.5×），耗时约 50ms。

**2. 翻译与视觉模型彻底解耦。**
4GB 显存放不下翻译大模型 + 检测器 + 修复模型，抢显存正是卡顿的根因。翻译层走
HTTP（DeepL 或本地 llama.cpp），永不与视觉阶段共享显存。一话对白只有几 KB，本地
模型即使 5 tok/s 也只是几分钟。

**3. 气泡内文字不需要 LaMa。**
气泡是纯色底，擦字取蒙版外围环形的中位数颜色直接填充即可，比生成式修复快两个
数量级且零显存。LaMa 仅在气泡底色非纯色时对小 crop 兜底 —— v1 跳过拟声词，
触发率极低。

## Web 界面

一条命令同时起前后端（在仓库根目录跑）：

```bash
powershell -File scripts\dev.ps1
```

或者手动开两个终端。**下面两条都在仓库根目录执行**，用 `--app-dir` / `--prefix`
指定子目录，就不需要 `cd` —— 也就不会因为当前已经在子目录里而失败：

```bash
python -m uvicorn ctt.server:app --port 8000 --app-dir backend
```

```bash
npm --prefix frontend run dev
```

打开 http://localhost:5173 ，三个页签：

- **翻译** —— 后端提供的目录浏览器（浏览器拿不到真实路径，只能由后端列目录）、
  递归子目录、**预览选中**、只跑前 N 页、分阶段实时进度、可取消
- **结果** —— 原图/成品切换、对白列表、直接改译文并重出片
- **配置** —— `ctt.toml` 全字段表单

技术栈 React 19 + Vite + Tailwind v4 + shadcn/ui，与 `swell-local-comic`、
`download-img` 一致，后续可直接加 `src-tauri` 变桌面版。

**配置表单是从后端反射生成的**：`GET /api/config` 返回字段的类型、可选值和说明，
前端据此渲染。往 Python dataclass 里加一个字段，UI 自动出现，不用改前端。

## Web 编辑器（旧版说明）

```bash
# 终端 1
cd backend && python -m uvicorn ctt.server:app --port 8000
# 终端 2
python -m http.server 5173 --directory frontend
```

打开 http://localhost:5173 ，填入 `out/project.cttproj` 点 Open。
可拖拽气泡、改译文/字号/字体/对齐、Auto-fit 复位、导出成品。

核心设计：**项目文档是唯一真相，渲染图是一次性的**。每次编辑都从原图重新合成，
所以反复调整不会像「在上一次渲染结果上继续画」那样逐渐劣化。

## 实测记录（本机 CPU，未启用 GPU）

| 阶段 | english.jpg 3200×2200 (2 气泡) | en2.jpg 720×10000 (3 气泡) |
|---|---|---|
| ocr | 28.5s (87%) | 4.7s (49%) |
| detect | 3.6s (11%) | 4.6s (48%) |
| typeset | 0.62s | 0.14s |
| erase | 0.15s | 0.12s |

OCR 识别率实测 **19/19 气泡全部读出**，置信度 0.87–1.00，西班牙语重音字符
（ESTÁS / CUÁNDO / SERÍA）正常。稳态约 1.3s/气泡。

检测与 OCR 目前都跑在 CPU 上。装 `onnxruntime-directml` 可直接提速且不需要
CUDA 工具链 —— `detect.py` 的 provider 优先级已经把 DirectML 排进去了。

## 踩过的坑（都已修复并有回归测试）

这几条都是排查花了时间、且不看输出图就发现不了的问题：

**RT-DETR 的 `orig_target_sizes` 是 `(宽, 高)` 不是 `(高, 宽)`。**
两种顺序在非正方形页面上都会输出「看起来合理」的框 —— 位置在图内、尺寸正常、
置信度高达 0.98，但全在错误的位置。在 `english.jpg` 上实测：`(w,h)` 找到 2/2 个
气泡，`(h,w)` 找到 0/2。

**变体字体的默认实例不一定是 Regular。**
`NotoSansSC-VF.ttf` 的默认实例是 **Thin**，直接加载会把对白渲染成发丝细的笔画。
`fonts.py` 现在显式指定 weight。排版数学与字重无关，所以没有别的测试能发现它。

**Otsu 阈值会在字形边缘留下鬼影。**
Otsu 把阈值切在墨色与纸色的中点，抗锯齿边缘被归到背景侧，填充后原文以灰色残影
形式保留。改成按「与背景色的距离」取蒙版才彻底擦干净。

**排版对字号不是单调的。**
字号变大 ⇒ 行高变大 ⇒ 每行落在气泡轮廓的不同位置，所以大字号可能装得下而小字号
装不下。二分查找假设单调，会返回远低于真实最大值的字号（实测 32 vs 79）。已改为
从上往下线性扫描。

**悬挂标点会让行宽校验否决自己刚生成的行。**
换行允许行尾标点悬挂出边界，但宽度校验若按完整宽度算就会拒绝该行，导致行数在
1/2 之间震荡 —— 这正是上面那条「非单调」的成因。

**文字框是矩形、气泡是圆的。**
框的四角会伸出气泡外，那里「偏离背景色」的判据选中的是气泡外的画面，填充就把
白色刷到了画上。现在擦除蒙版一律被裁剪到 `bubble_interior` 之内。

**追踪气泡内部不能用灰度阈值。**
白气泡叠在浅色背景上时，Otsu 会把两者合并，轮廓同时吞掉气泡和背景。改为从
文字框外围环形采样气泡填充色，再按颜色距离做连通域。

**PaddleOCR 3.x 没有 `lang='latin'`。** 会报 "No models are available"。用 `'en'`，
它对西班牙语重音字符也识别正常。

**PaddleOCR 韩文模型在 paddle 3.3.1 上崩溃。** 报
`ConvertPirAttribute2RuntimeAttribute not support`。加 `enable_mkldnn=False` 解决。
英文模型恰好不触发，所以看起来像是「韩文不支持」，实际是 oneDNN 后端的 bug。

**拖拽气泡不能改 `box`。** `box` 是原文位置，同时也是擦除的锚点。把位移写进 `box`
会让擦除跑到别处去，原文就以鬼影形式从新译文上方浮出来。现在位移记在独立的
`offset` 字段，只在渲染时生效。

**一个气泡只能被一个文字块认领。** 排版会把每个块撑满其容器，所以两个块共用一个
气泡时会各自铺满、叠在一起。这不是假设：角色名标签（LIAM / EMMA）就画在气泡旁边，
被拉进邻居气泡后会以撑满气泡的字号横盖在对白上。现在按 containment 取最高者，
落选的退回自己的 `box`——那本来就是标签该待的地方。

**「漏译英文」不能靠大小写判断。** 漫画原文全大写，所以模型漏译时那个词也是大写的，
和拟声词长得一样。只查小写会漏掉 `OH SHIT!` 却抓到 `gonna`。改用字母多样性区分：
拉长的叫喊（`AAAAIIIIIEEEE`）用很少的字母铺满很多位置，真词几乎每位都换字母。

**重译要从原文重来，不能让模型改自己的答案。** 把first-pass 的中文丢回去说「请修正」，
模型会原样吐回来（实测两次字节相同）。改成重新给它英文原文 + 点名漏掉的词才有效。

**prompt 里要写语言名，不能写语言代码。** 把 BCP-47 标签 `zh-Hans` 直接塞进 prompt，
实测一页 5 个气泡里 4 个输出繁体——标签是给机器看的，7B 模型不能可靠地把它解码成
「写简体」。改成 `Simplified Chinese (简体中文)` 后繁体问题消失。另加了
`traditional_chars()` 做确定性兜底，模型再跑偏也会进 `needs_review` 而不是直接出片。

**省略号不能按字符转全角。** `.` → `。` 的逐字符映射会把 `wait...` 变成 `wait。。。`，
中文里这是刺眼的排版错误。要先把连续点号整体替换成 `……`。

**拉丁词禁止断行不能只退到空格。** 中文里嵌的英文单词两侧没有空格，所以
`太sexy了` 断成了 `太se` / `xy了`。要退到该词自身的起始位置，不是上一个空格。

**从项目文档重新出片必须重跑擦除。** `.cttproj` 存的是文字和几何，不是像素。
直接对原图跑 `render_page` 会把译文画在没擦掉的原文上面——两层字叠在一起。
正确顺序永远是 `erase` → `typeset`。

**`threading.Lock` 不可重入。** `JobManager.submit` 持锁期间调用了同样要加锁的
`busy` 属性，直接死锁——提交任务的那个 HTTP 请求永远不返回，后端日志里连这条
请求都不会出现（因为它卡在处理线程里）。拆出一个 `_busy_unlocked()` 给持锁方
调用。`tests/test_jobs.py` 里有一条专门守这个的用例。

**输出目录不能用 `prev || 默认值` 记忆。** 那样它只会在第一次赋值，用户把输入
目录浏览到别处后输出仍指向最初打开的那个目录（实测停在了用户主目录），结果会
静默写错地方。要用一个 ref 记录「用户是否手改过」，没改过就一直跟随输入目录。

**文档里的启动命令别写 `cd 子目录 && ...`。** 已经在那个子目录里的时候就会报
`找不到 backend\backend`。用 `--app-dir` / `--prefix` 指定目录，命令在哪都能跑。
`scripts/dev.ps1` 同理，所有路径相对脚本自身解析。

**TOML 里节标题以下的裸键属于该节。** 往 `ctt.toml` 加 `[input]` 时把它插在了
`models_dir` 上面，结果那个顶层键变成了 `input.models_dir`，静默失效。
是「未知配置项」警告抓到的——这条警告的价值就在这里，否则只会表现为
「我明明配了却不生效」。

## 状态

**全部 84 个测试通过。** 已在真实素材上验证：切片、检测、OCR、蒙版、擦除、排版、
翻译降级链、FastAPI 服务、Web 编辑器（打开项目 / 拖拽 / 改译文与字号 / Auto-fit
复位 / 导出，全部实测跑通）。

翻译后端只用 stub 验证过接线，**真实 DeepL / 本地 LLM 调用未实测**（需要 API key
或本地服务）。降级链逻辑本身有 20 个单测覆盖。

已知待办：扫图组的「制作名单」面板会被检测成对白（en4.jpg 的 b006）。现在会被
`looks_like_credits` 标记进 needs_review 而不是静默翻译，但不会自动删除。

## 翻译后端：本地 LLM（当前推荐）

`llama-cpp-python` + `Qwen2.5-7B-Instruct-abliterated-v2` Q4_K_M（4.36 GB）。
**进程内库，不装后台服务**，跑完即释放。

```bash
pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
python -m ctt.cli translate <dir> -o out/ --translators llamacpp
```

模型首次自动下到 `backend/models/gguf/`，方便整目录删除。

**不影响玩游戏**，实测数据：

| | 显存 |
|---|---|
| 空闲基线（含已开着的 LoL/斗鱼/Chrome） | 2,951 MB |
| 跑 10 页期间峰值 | 3,210 MB |
| **本管线贡献** | **约 259 MB** |

`n_gpu_layers=0` 让 llama.cpp 完全不碰显卡。那 259 MB 是 ONNX Runtime 的 CPU 侧
分配加噪声。基准测试本身就是在 `League of Legends.exe` 开着的情况下跑完的。

性能（CPU，12 逻辑核用 6 线程）：

| | 冷启动首测 | 热状态 |
|---|---|---|
| 模型加载 | 含在总时长里 | 4.6s（一次） |
| 每页 | — | 12.6s |
| 全流程每页（含检测+OCR） | — | 38.7s |
| 284 页 | — | 约 183 分钟 |

质量对比见下节。仍有残留问题：7B 模型偶尔漏译英文俚语，`GONNA GET WILD` 连试两次
都没译出来。这类行会被 `residual_latin` 检出并进 `needs_review`，交给编辑器人工处理。

## 翻译后端：NLLB 实测结论（已弃用）

**NLLB-200 不能用于成人素材。** 已实测并移除。它的失败方式不是生硬，是**静默删词**——
训练语料做过内容过滤，词表里没有那些词，生成时直接跳过：

| 原文 | 输出 | 丢失 |
|---|---|---|
| HE'S BUSY **FUCKING** ME NOW | 他现在忙着我 | 动词消失 |
| I LOVE YOUR **DICK** | 我爱你的**子** | 名词消失 |
| THANKS FOR YOUR HARD **COCK**, HONEY | 谢谢你的硬，**蜜蜂** | 名词消失 |

人名同样不可靠：`LIAM` 在不同页被译成 连接 / 莱姆 / 姆 / **谎言**（读成了 lyin'）。

同一批句子换成本地 LLM 后：

| 原文 | NLLB | llama.cpp (Qwen2.5-7B) |
|---|---|---|
| LIAM | 连接 | **利亚姆**（术语表生效） |
| I LOVE YOUR **DICK** | 我爱你的**子** | 我喜欢你的**阳具** |
| THANKS FOR YOUR HARD **COCK**, HONEY | 谢谢你的硬，**蜜蜂** | 谢谢你**坚硬的棒子**，**亲爱的** |
| I'M ALSO YOUR **GIRL** | 你的**女儿**（错） | 你的**女孩** |
| DON'T **CUM** YET, LIAM | 别，姆，给她一个好东西 | **不要射，利亚姆。好好给她！** |

## 已知缺口

**术语表在 NLLB / DeepL 档只检测不注入。** `pipeline._translate` 直接调 translator，
术语表仅事后比对 `violations`。LLM 档不受影响——`llamacpp.py` 通过 `as_prompt_hint`
把术语注入 prompt，实测 `LIAM → 利亚姆` 生效。

## 卸载

```bash
powershell -File scripts\uninstall.ps1 -LlmOnly -WhatIf   # 只退 LLM 档，先干跑
powershell -File scripts\uninstall.ps1 -LlmOnly           # 释放约 4.4 GB
```

`-LlmOnly` 是「试了不满意就退回去」的路径：只删 `llama-cpp-python` + GGUF，
检测 / OCR / 排版原样保留，管线自动降级到 `--translators` 里的下一档。

完整卸载（连检测器和 OCR 一起）：

```bash
powershell -File scripts\uninstall.ps1 -WhatIf
powershell -File scripts\uninstall.ps1
```

包清单是安装前后 `pip list` 的实测差集（`scripts/packages-*.txt`），不是猜的。
两条保护逻辑，都是踩过才加的：

- **不碰 `~/.cache/huggingface` 整体。** 该缓存与其它工具共享——本机上就有个 7 月的
  1.6GB `stabilityai/TripoSR`，一句 `rm -rf` 会连它一起删。只按名字删本项目建的条目。
- **依赖检查迭代到不动点，而不是单遍。** 保留的候选包会成为「保留它的依赖」的理由：
  `Jinja2` 因 fastapi 被保留 ⇒ `MarkupSafe` 也必须留，尽管候选集外没人直接引用它。
  单遍扫描会删掉 MarkupSafe，留下一个坏掉的 Jinja2。

## v1 明确不做

- 拟声词（`text_free`）翻译与渲染 —— 已检出并存入 proj，v2 处理。
  `result/` 里那种手绘渐变描边效果全自动做不到。
- 竖排日文渲染 —— 当前素材无日漫，`layout.py` 预留了 `vertical` 开关。
