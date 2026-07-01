/**
 * 平台公告(v5 item5)—— 创始人致辞。
 *
 * 只需改这里的文案 / 把 id 递增(bump),即视为一条"新公告",
 * 已 dismiss 过旧 id 的用户会重新看到。dismiss 状态存 localStorage。
 */

export interface PlatformAnnouncement {
  /** 版本 id —— 改文案时递增,让老用户重新看到 */
  id: string;
  /** 一句话摘要(收起态显示) */
  teaser: string;
  /** 标题(展开态) */
  title: string;
  /** 正文段落(展开态,逐段渲染) */
  paragraphs: string[];
  /** 落款 */
  signature: string;
  /** CTA 文案 */
  ctaLabel: string;
}

export const PLATFORM_ANNOUNCEMENT: PlatformAnnouncement = {
  id: "2026-07-founder-byok",
  teaser: "创始人的话:平台公用额度有限,每月 ¥5 开通「自携密钥」即可用满全部功能。",
  title: "来自浑晶创始人的一封信",
  paragraphs: [
    "各位创作者,你们好。我是浑晶的创始人。",
    "先说一句大实话:平台的「公用 API 额度」目前非常有限 —— 我个人还没有足够的资金为大家持续充值公共模型账户,所以每月赠送的免费额度,多数时候可能不太够用,甚至暂时用不上。这一点我不想藏着掖着。",
    "但完全不用慌。浑晶从一开始就支持「自携密钥(BYOK)」:你接入自己的大模型 API key(文本 + 生图都行),每月只需 ¥5 辛苦费,就能解锁平台全部五大创作态(含漫创态),而且完全不占用平台公用额度 —— 用多少花多少,真正把顶级平台的能力握在自己手里。",
    "开通方式很简单:点击左下角你的头像 → 展开小卡片 → 「自携密钥」→ ¥5 即可用满一个月。",
    "谢谢你选择浑晶,也谢谢你的理解与支持。我们一起,把它做成真正属于创作者的工具。",
  ],
  signature: "—— 浑晶创始人",
  ctaLabel: "¥5 开通自携密钥 →",
};

/** localStorage key —— 每条公告独立记 dismiss */
export function announcementDismissKey(id: string): string {
  return `hj_announce_dismissed_${id}`;
}
