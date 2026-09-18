/**
 * 发送消息的「即时出现」信号通道。
 *
 * 用户点击发送的那一刻后端还没有返回任何数据，侧边栏列表也可能还是旧的。
 * 这里用一个极轻量的发布/订阅通道把这个「刚发送」事件同步广播出去，
 * 让侧边栏对话导航项在发送瞬间就播放出现动画，
 * 而不必等待后端响应、列表刷新或整页跳转。
 *
 * 顶部栏复用同一份「刚发送」事实，但它只依赖本地 state 重播动画，
 * 不从这里消费 token，因此不存在两个消费者互相抢信号的问题。
 */

interface RevealToken {
  /** 对话 id；空字符串表示「新任务」页首次发送时尚未拿到 threadId。 */
  id: string;
  /** 同一对话的第几次发送，用来支持同一条对话上的重复动画。 */
  seq: number;
}

const tokens: Array<RevealToken> = [];
const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach((listener) => listener());
}

/** 订阅出现信号，返回值可直接作为 useEffect 清理函数。 */
export function subscribeThreadReveal(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * 标记「这条对话刚刚发了消息」。
 * @param threadId 对话 id；新任务页首次发送时传空字符串，等待真实 id 认领。
 */
export function markThreadReveal(threadId: string): void {
  const seq = (tokens.filter((token) => token.id === threadId).at(-1)?.seq ?? 0) + 1;
  tokens.push({ id: threadId, seq });
  emit();
}

/** 认领一次性出现信号；返回 seq 表示需要播放动画，返回 null 表示无待播放动画。 */
export function consumeThreadReveal(threadId: string): number | null {
  const index = tokens.findIndex((token) => token.id === threadId);
  if (index < 0) return null;
  const [token] = tokens.splice(index, 1);
  return token?.seq ?? null;
}

/** 把新任务页留下的匿名信号转交给真实的 threadId。 */
export function claimAnonymousThreadReveal(threadId: string): boolean {
  const index = tokens.findIndex((token) => token.id === "");
  if (index < 0) return false;
  tokens.splice(index, 1);
  markThreadReveal(threadId);
  return true;
}

/** 是否存在等待认领的匿名信号。 */
export function hasAnonymousThreadReveal(): boolean {
  return tokens.some((token) => token.id === "");
}
