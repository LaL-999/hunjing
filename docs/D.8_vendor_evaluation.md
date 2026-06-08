# Sprint D.8 横评报告 — Doubao Seedream + Qwen-VL

**生成时间**:2026-05-12T17:47:10.442421
**模式**:真实调用
**绘图总成本**:¥1.6
**视觉总成本**:¥0.0827
**总计**:¥1.6827

---

## 评分指引

浏览器打开每张图 URL → 主观评分(1-5)填到对应行 `评分:_/5`:
- **5** = 卓越,可直接进生产
- **4** = 可用,微调即上
- **3** = 一般,需迭代多轮才能用
- **2** = 差强人意,可能需换 vendor
- **1** = 不可用,直接淘汰

评分填完后:**绘图均分 ≥ 4 且视觉均分 ≥ 4** → D.8 验收通过,定 Seedream + Qwen-VL
为 D.9 漫画态主路由;否则需进入「调参 / 换 vendor」循环。

## A. 绘图(Doubao Seedream)

### C1 — 2D 国漫工笔 林黛玉葬花
- **测试维度**:古典文学 + 中国国漫工笔风(漫画主战场之古风类)
- **画幅**:3:4
- **本 case 总成本**:¥0.2000

#### C1.1
- **prompt**:【2D 国漫工笔风格,鲜艳半厚涂,精致面部刻画,氛围光效,中国网络漫画封面美学】一位 17 岁的古代少女,身形清瘦如弱柳,正手持小锄在飘满落花的桃树下葬花。水墨晕染背景,落英缤纷,人物表情忧郁。禁止真人写实摄影,禁止 3D 渲染,禁止超写实皮肤质感。
- **图像 URL**:https://ark-content-generation-v2-cn-beijing.tos-cn-beijing.volces.com/doubao-seedream-4-0/021778579112220071b8e0edac031ecb87fdc9a38ce70a915a910_0.jpeg?X-Tos-Algorithm=TOS4-HMAC-SHA256&X-Tos-Credential=AKLTYWJkZTExNjA1ZDUyNDc3YzhjNTM5OGIyNjBhNDcyOTQ%2F20260512%2Fcn-beijing%2Ftos%2Frequest&X-Tos-Date=20260512T094521Z&X-Tos-Expires=86400&X-Tos-Signature=a41ee5795ef89f30ac5105d83747ff025700e99027aba0d1dc678a29b7455d74&X-Tos-SignedHeaders=host
- **耗时**:13.16s
- **成本**:¥0.2000
- **评分**:5/5
- **备注**:_(可选,记录画风偏差 / 角色失真等)_

### C2 — 2D 日系动漫 都市西装男
- **测试维度**:现代都市 + 日系动漫风(都市言情漫画)
- **画幅**:16:9
- **本 case 总成本**:¥0.2000

#### C2.1
- **prompt**:【2D 日系动漫风格,赛璐璐上色,清晰线稿,日漫人物比例,大眼睛精致面部】一位中年男人穿深灰色西装,坐在落地窗前的咖啡馆,黄昏阳光从侧后方照入,桌上一杯黑咖啡,他低头看着手机,表情平静。禁止真人写实摄影,禁止 3D 渲染,禁止超写实皮肤质感。
- **图像 URL**:https://ark-content-generation-v2-cn-beijing.tos-cn-beijing.volces.com/doubao-seedream-4-0/021778579122801c710b3d771f5011ef7eccce758c790d26ce2da_0.jpeg?X-Tos-Algorithm=TOS4-HMAC-SHA256&X-Tos-Credential=AKLTYWJkZTExNjA1ZDUyNDc3YzhjNTM5OGIyNjBhNDcyOTQ%2F20260512%2Fcn-beijing%2Ftos%2Frequest&X-Tos-Date=20260512T094532Z&X-Tos-Expires=86400&X-Tos-Signature=8ef3aaa13381225dbe52dbb40878fb86bef3eb758bb3a907a9f95b8b72e90632&X-Tos-SignedHeaders=host
- **耗时**:11.64s
- **成本**:¥0.2000
- **评分**:1/5
- **备注**:烂大街的人机感图片，没有独特性

### C3 — 2D 二次元 同角色三表情一致性测试
- **测试维度**:角色一致性 + 2D 二次元(测 Seedream 同 prompt 不同表情的形象稳定度)
- **画幅**:1:1
- **本 case 总成本**:¥0.6000

#### C3.1
- **prompt**:【2D 二次元动漫风格,扁平上色,清晰线稿,日漫大眼睛】一位 20 岁的红发女孩,翠绿色眼睛,白色 T 恤,身材纤瘦,正在大笑,眼睛弯成月牙。禁止真人写实摄影,禁止 3D 渲染,禁止超写实皮肤质感。
- **图像 URL**:https://ark-content-generation-v2-cn-beijing.tos-cn-beijing.volces.com/doubao-seedream-4-0/021778579134395f1d695eca3dfe97a619875923d1ba6845f026c_0.jpeg?X-Tos-Algorithm=TOS4-HMAC-SHA256&X-Tos-Credential=AKLTYWJkZTExNjA1ZDUyNDc3YzhjNTM5OGIyNjBhNDcyOTQ%2F20260512%2Fcn-beijing%2Ftos%2Frequest&X-Tos-Date=20260512T094540Z&X-Tos-Expires=86400&X-Tos-Signature=7cca5984a37d34a9dc4a5f6b02d22bc1269906f6c9bc5c1cebcba135b60dc795&X-Tos-SignedHeaders=host
- **耗时**:8.13s
- **成本**:¥0.2000
- **评分**:2/5
- **备注**:图片中的元素和背景单调

#### C3.2
- **prompt**:【2D 二次元动漫风格,扁平上色,清晰线稿,日漫大眼睛】一位 20 岁的红发女孩,翠绿色眼睛,白色 T 恤,身材纤瘦,愤怒地皱眉,牙齿咬紧。禁止真人写实摄影,禁止 3D 渲染,禁止超写实皮肤质感。
- **图像 URL**:https://ark-content-generation-v2-cn-beijing.tos-cn-beijing.volces.com/doubao-seedream-4-0/02177857914253585a2895c3c7af348203da6589a4be3123a040d_0.jpeg?X-Tos-Algorithm=TOS4-HMAC-SHA256&X-Tos-Credential=AKLTYWJkZTExNjA1ZDUyNDc3YzhjNTM5OGIyNjBhNDcyOTQ%2F20260512%2Fcn-beijing%2Ftos%2Frequest&X-Tos-Date=20260512T094553Z&X-Tos-Expires=86400&X-Tos-Signature=17759b7a0ca23e6804a1b7bc9c4c9a7dd7f25c2ded843e2c4e4a38308bc6f107&X-Tos-SignedHeaders=host
- **耗时**:12.91s
- **成本**:¥0.2000
- **评分**:2/5
- **备注**:同上

#### C3.3
- **prompt**:【2D 二次元动漫风格,扁平上色,清晰线稿,日漫大眼睛】一位 20 岁的红发女孩,翠绿色眼睛,白色 T 恤,身材纤瘦,流泪哭泣,眼角通红。禁止真人写实摄影,禁止 3D 渲染,禁止超写实皮肤质感。
- **图像 URL**:https://ark-content-generation-v2-cn-beijing.tos-cn-beijing.volces.com/doubao-seedream-4-0/02177857915542743cc1cf6ced7e437334ae832707c282662740b_0.jpeg?X-Tos-Algorithm=TOS4-HMAC-SHA256&X-Tos-Credential=AKLTYWJkZTExNjA1ZDUyNDc3YzhjNTM5OGIyNjBhNDcyOTQ%2F20260512%2Fcn-beijing%2Ftos%2Frequest&X-Tos-Date=20260512T094610Z&X-Tos-Expires=86400&X-Tos-Signature=a49cfa48361e0d785c035fffa7a77a6296c13ec57494896c931697afe21048bf&X-Tos-SignedHeaders=host
- **耗时**:17.13s
- **成本**:¥0.2000
- **评分**:2/5
- **备注**:同上

### C4 — 2D 国漫古风 双人对话花园
- **测试维度**:多角色构图 + 2D 国漫古风(漫画分镜核心)
- **画幅**:16:9
- **本 case 总成本**:¥0.2000

#### C4.1
- **prompt**:【2D 国漫古风,半厚涂,鲜艳色彩,中国网络漫画封面美学,精致服饰刻画】一位穿白色长裙的年轻女子和一位穿藏蓝长袍的中年男人,站在春日花园的石径两侧,面对面交谈。樱花飘落,远处亭台楼阁,氛围唯美。禁止真人写实摄影,禁止 3D 渲染,禁止超写实皮肤质感。
- **图像 URL**:https://ark-content-generation-v2-cn-beijing.tos-cn-beijing.volces.com/doubao-seedream-4-0/021778579172589bf18d587e2f99cd9673501e40cd28894320737_0.jpeg?X-Tos-Algorithm=TOS4-HMAC-SHA256&X-Tos-Credential=AKLTYWJkZTExNjA1ZDUyNDc3YzhjNTM5OGIyNjBhNDcyOTQ%2F20260512%2Fcn-beijing%2Ftos%2Frequest&X-Tos-Date=20260512T094622Z&X-Tos-Expires=86400&X-Tos-Signature=fa891508b9f53d22ef79fefc4bb25c1dd212278f618fe9c5e97962c8eec486d3&X-Tos-SignedHeaders=host
- **耗时**:11.86s
- **成本**:¥0.2000
- **评分**:3/5
- **备注**:人物显得幼稚

### C5 — 2D 插画 镜头语言对照(远景 vs 特写)
- **测试维度**:镜头语义 + 2D 插画风(漫画导演 agent 出 prompt 的关键能力)
- **画幅**:3:4
- **本 case 总成本**:¥0.4000

#### C5.1
- **prompt**:【2D 插画风格,扁平笔触,鲜艳色块,无写实质感】极远景镜头:一座孤独的灯塔矗立在悬崖边,海浪汹涌,黄昏天空,画面构图聚焦灯塔与广阔环境的对比。禁止真人写实摄影,禁止 3D 渲染,禁止超写实皮肤质感。
- **图像 URL**:https://ark-content-generation-v2-cn-beijing.tos-cn-beijing.volces.com/doubao-seedream-4-0/021778579184458ebdc946f2be2eb9a87f08a675dc2838593c33c_0.jpeg?X-Tos-Algorithm=TOS4-HMAC-SHA256&X-Tos-Credential=AKLTYWJkZTExNjA1ZDUyNDc3YzhjNTM5OGIyNjBhNDcyOTQ%2F20260512%2Fcn-beijing%2Ftos%2Frequest&X-Tos-Date=20260512T094634Z&X-Tos-Expires=86400&X-Tos-Signature=23c4b0a12e1964c04d54980bb26b1ef48090e1ad8a7215569cde2a9b6a212f61&X-Tos-SignedHeaders=host
- **耗时**:11.55s
- **成本**:¥0.2000
- **评分**:5/5
- **备注**:_(可选,记录画风偏差 / 角色失真等)_

#### C5.2
- **prompt**:【2D 插画风格,扁平笔触,鲜艳色块,无写实质感】极特写镜头:同一座灯塔的窗户细节,玻璃反光,锈迹斑斑,海雾笼罩,画面只填灯塔窗户一角占满。禁止真人写实摄影,禁止 3D 渲染,禁止超写实皮肤质感。
- **图像 URL**:https://ark-content-generation-v2-cn-beijing.tos-cn-beijing.volces.com/doubao-seedream-4-0/021778579196019e63fcd4b1459546935812764a3dda63c3c45c3_0.jpeg?X-Tos-Algorithm=TOS4-HMAC-SHA256&X-Tos-Credential=AKLTYWJkZTExNjA1ZDUyNDc3YzhjNTM5OGIyNjBhNDcyOTQ%2F20260512%2Fcn-beijing%2Ftos%2Frequest&X-Tos-Date=20260512T094647Z&X-Tos-Expires=86400&X-Tos-Signature=2bed6566ff8b6100ae3c6ea767c16eab5e97880c733f715648abb5b81906d076&X-Tos-SignedHeaders=host
- **耗时**:13.04s
- **成本**:¥0.2000
- **评分**:1/5
- **备注**:直接给我生成了一个巨大的人头，很诡异

## B. 多模态视觉(Qwen-VL)

### Q1 — 基础识别 — 人数 + 衣着颜色
- **测试维度**:是否能数清主体 + 颜色识别(漫画质检最低要求)
- **测试图**:https://ark-content-generation-v2-cn-beijing.tos-cn-beijing.volces.com/doubao-seedream-4-0/021778579172589bf18d587e2f99cd9673501e40cd28894320737_0.jpeg?X-Tos-Algorithm=TOS4-HMAC-SHA256&X-Tos-Credential=AKLTYWJkZTExNjA1ZDUyNDc3YzhjNTM5OGIyNjBhNDcyOTQ%2F20260512%2Fcn-beijing%2Ftos%2Frequest&X-Tos-Date=20260512T094622Z&X-Tos-Expires=86400&X-Tos-Signature=fa891508b9f53d22ef79fefc4bb25c1dd212278f618fe9c5e97962c8eec486d3&X-Tos-SignedHeaders=host
- **问题**:请如实回答:图中有几个人?各自穿着什么颜色的衣服?
- **理想答案要点**:2 人;白色长裙;藏蓝长袍
- **Qwen-VL 回答**:
  > 图中有两个人。
  > 
  > - 左边是一位年轻女性，穿着白色的连衣裙。
  > - 右边是一位年长男性，穿着深蓝色的传统长袍。
- **成本**:¥0.0256
- **评分**:5/5(命中要点 + 表达清晰 = 5;漏要点或啰嗦 = 3;答非所问 = 1)
- **备注**:_

### Q2 — 高阶语义 — 情绪
- **测试维度**:情绪识别(漫画质检验证表情符合分镜意图)
- **测试图**:https://ark-content-generation-v2-cn-beijing.tos-cn-beijing.volces.com/doubao-seedream-4-0/021778579172589bf18d587e2f99cd9673501e40cd28894320737_0.jpeg?X-Tos-Algorithm=TOS4-HMAC-SHA256&X-Tos-Credential=AKLTYWJkZTExNjA1ZDUyNDc3YzhjNTM5OGIyNjBhNDcyOTQ%2F20260512%2Fcn-beijing%2Ftos%2Frequest&X-Tos-Date=20260512T094622Z&X-Tos-Expires=86400&X-Tos-Signature=fa891508b9f53d22ef79fefc4bb25c1dd212278f618fe9c5e97962c8eec486d3&X-Tos-SignedHeaders=host
- **问题**:图中两位人物的情绪状态分别是什么?是友好交谈、争吵、还是其他?
- **理想答案要点**:平静交谈 / 友好;非冲突态
- **Qwen-VL 回答**:
  > 从图中两位人物的姿态和表情来看，他们的情绪状态是**平和而友好**的，更倾向于一种**安静、深情的交谈**，而非争吵或其他激烈情绪。
  > 
  > 具体分析如下：
  > 
  > 1. **面部表情**：
  >    - 女孩面带柔和的微笑，眼神专注地看着对面的老人，神情温婉而略带敬意。
  >    - 老人面容沉静，目光温和地注视着女孩，嘴角微扬，流露出慈祥与关怀。
  > 
  > 2. **身体姿态**：
  >    - 两人面对面站立，距离适中，没有紧张或对抗的姿势。
  >    - 女孩双手自然交叠在身前，姿态端庄；老人站姿稳重，衣袍飘逸，显得从容不迫。
  > 
  > 3. **环境氛围**：
  >    - 樱花纷飞，春意盎然，亭台楼阁掩映在花树之间，整体画面充满诗意与宁静。
  >    - 这样的背景通常象征着温馨、回忆或重要对话的场景，强化了情感交流的温柔基调。
  > 
  > 综合来看，这是一幅描绘**长辈与晚辈之间深情对话**的画面，可能是在传递教诲、分享人生感悟，或是表达亲情与思念。气氛和谐，情绪温暖，属于**友好交谈**，甚至带有某种**感性与庄重的情感交流**。
- **成本**:¥0.0308
- **评分**:5/5(命中要点 + 表达清晰 = 5;漏要点或啰嗦 = 3;答非所问 = 1)
- **备注**:_

### Q3 — 合规扫描 — negative case
- **测试维度**:质检 agent 核心:对正常图能输出'无敏感'(关键!不能滥报)
- **测试图**:https://ark-content-generation-v2-cn-beijing.tos-cn-beijing.volces.com/doubao-seedream-4-0/021778579172589bf18d587e2f99cd9673501e40cd28894320737_0.jpeg?X-Tos-Algorithm=TOS4-HMAC-SHA256&X-Tos-Credential=AKLTYWJkZTExNjA1ZDUyNDc3YzhjNTM5OGIyNjBhNDcyOTQ%2F20260512%2Fcn-beijing%2Ftos%2Frequest&X-Tos-Date=20260512T094622Z&X-Tos-Expires=86400&X-Tos-Signature=fa891508b9f53d22ef79fefc4bb25c1dd212278f618fe9c5e97962c8eec486d3&X-Tos-SignedHeaders=host
- **问题**:请检查:图中是否包含血腥、暴力、色情、政治敏感等违规内容?有就指出,没有就明确说没有。
- **理想答案要点**:应回答:没有违规内容(明确否定,不模糊)
- **Qwen-VL 回答**:
  > 没有。图中未包含血腥、暴力、色情、政治敏感等违规内容。画面描绘的是一个宁静优美的春日场景，两位人物在樱花盛开的庭院中对视，氛围平和温馨，符合传统东方美学风格。
- **成本**:¥0.0263
- **评分**:5/5(命中要点 + 表达清晰 = 5;漏要点或啰嗦 = 3;答非所问 = 1)
- **备注**:_

---

## D.8 验收决策(创始人填)

- **绘图均分**:3/5
- **视觉均分**:5/5
- **是否定 Seedream + Qwen-VL 为 D.9 漫画态主路由?**:☐ 是 / ☐ 否(改 __是___)
- **额外 fallback 需求?**:☐ 不需要 / ☐ 需要 __不需要___
