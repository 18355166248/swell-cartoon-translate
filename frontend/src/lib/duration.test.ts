import { describe, expect, it } from "vitest";
import { formatDuration, formatFinishTime, formatRemaining } from "./duration";

describe("formatDuration", () => {
  it("shows seconds under a minute", () => {
    expect(formatDuration(0)).toBe("0秒");
    expect(formatDuration(45)).toBe("45秒");
  });

  it("shows minutes only when there is no hour", () => {
    // "0小时12分" is what this exists to avoid.
    expect(formatDuration(12 * 60)).toBe("12分");
    expect(formatDuration(59 * 60)).toBe("59分");
  });

  it("shows hours and minutes together", () => {
    expect(formatDuration(3600 + 23 * 60)).toBe("1小时23分");
    expect(formatDuration(3 * 3600 + 5 * 60)).toBe("3小时5分");
  });

  it("drops a zero minute component", () => {
    expect(formatDuration(2 * 3600)).toBe("2小时");
  });

  it("carries instead of printing 60 minutes", () => {
    // 1h 59m 45s rounds the minutes to 60.
    expect(formatDuration(3600 + 59 * 60 + 45)).toBe("2小时");
  });

  it("handles missing and negative input", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(undefined)).toBe("—");
    expect(formatDuration(NaN)).toBe("—");
    expect(formatDuration(-10)).toBe("0秒");
  });
});

describe("formatFinishTime", () => {
  const now = new Date(2026, 7, 16, 14, 30, 0);

  it("gives the wall-clock finish time", () => {
    expect(formatFinishTime(30 * 60, now)).toBe("15:00");
    expect(formatFinishTime(2 * 3600 + 12 * 60, now)).toBe("16:42");
  });

  it("marks the next day", () => {
    // A long run started in the evening is confusing without this.
    expect(formatFinishTime(11 * 3600, now)).toBe("明天 01:30");
  });

  it("marks further-out days", () => {
    expect(formatFinishTime(50 * 3600, now)).toBe("2天后 16:30");
  });

  it("handles missing input", () => {
    expect(formatFinishTime(null, now)).toBe("—");
  });
});

describe("formatRemaining", () => {
  const now = new Date(2026, 7, 16, 14, 30, 0);

  it("combines duration and finish time", () => {
    expect(formatRemaining(3600 + 12 * 60, now)).toBe("还需 1小时12分 · 预计 15:42 完成");
  });

  it("says so when there is no estimate yet", () => {
    // Before any page completes there is no sample to extrapolate from.
    expect(formatRemaining(null, now)).toBe("预计时间计算中");
  });
});
