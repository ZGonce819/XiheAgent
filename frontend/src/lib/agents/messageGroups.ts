import type { Message, TodoChunk } from "./types";

function hasTools(message: Message): boolean {
  return message.chunks.some((chunk) => chunk.kind === "tool-execution");
}

/** Group execution phases without moving a final answer into their details. */
export function groupConversationMessages(messages: Message[]): Message[] {
  const result: Message[] = [];
  let turn: Message[] = [];

  const flushTurn = () => {
    if (!turn.length) return;
    let latestPlan: TodoChunk | undefined;
    const phases = turn.map((message) => {
      for (const chunk of message.chunks) {
        if (chunk.kind === "todo") latestPlan = chunk;
      }
      // Older live plan messages include a synthetic heading, not model prose.
      const planOnly = message.chunks.some((chunk) => chunk.kind === "todo")
        && message.chunks.every((chunk) => chunk.kind === "todo"
          || (chunk.kind === "text" && chunk.text === "任务计划"));
      return { ...message, chunks: planOnly ? [] : message.chunks.filter((chunk) => chunk.kind !== "todo") };
    }).filter((message) => message.chunks.length > 0);

    const rows: Message[] = [];
    const turnHasTools = phases.some(hasTools);
    let markedNoToolExecution = false;
    for (let index = 0; index < phases.length; index += 1) {
      const phase = phases[index]!;
      const next = phases[index + 1];
      const noToolModelExecution = !turnHasTools
        && phase.author === "agent"
        && !markedNoToolExecution;
      const execution = Boolean(phase.executionNarration || hasTools(phase)
        || (next && hasTools(next)) || noToolModelExecution);
      if (noToolModelExecution) markedNoToolExecution = true;
      const previous = rows[rows.length - 1];
      if (execution && previous?.executionNarration) {
        rows[rows.length - 1] = { ...previous, chunks: [...previous.chunks, ...phase.chunks] };
      } else {
        rows.push({ ...phase, executionNarration: execution });
      }
    }
    if (latestPlan) {
      const activityIndex = rows.findLastIndex((row) => row.executionNarration);
      if (activityIndex >= 0) {
        const activity = rows[activityIndex]!;
        rows[activityIndex] = { ...activity, chunks: [...activity.chunks, latestPlan] };
      } else {
        rows.unshift({ ...turn[0]!, id: `plan-${turn[0]!.id}`, executionNarration: true, chunks: [latestPlan] });
      }
    }
    result.push(...rows);
    turn = [];
  };

  for (const message of messages) {
    if (message.hidden) continue;
    if (message.author === "agent" || message.author === "tool") turn.push(message);
    else {
      flushTurn();
      result.push(message);
    }
  }
  flushTurn();
  return result;
}
