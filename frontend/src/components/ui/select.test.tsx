import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { Select, SelectContent, SelectItem, SelectTrigger } from "./select"

afterEach(cleanup)

/**
 * 下拉箭头的翻转动画由两部分组成：
 *
 * - 角度：`agents.css` 里的 `[data-slot="select-trigger"][data-popup-open] > svg`；
 * - 过渡：触发器图标自身的 `transition-transform duration-200 ease-out`。
 *
 * 两者缺一不可 —— 少了过渡，箭头会瞬间跳转而不是旋转，与项目里其它 CaretDown
 * 不一致（它们统一都是 `transition-transform duration-200`）。这里锁住过渡，
 * 避免再次被删掉。
 */
describe("SelectTrigger 下拉箭头", () => {
  function renderSelect() {
    return render(
      <Select value="a" onValueChange={() => {}}>
        <SelectTrigger aria-label="测试选择">
          <span>选项 A</span>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="a">选项 A</SelectItem>
        </SelectContent>
      </Select>
    )
  }

  it("箭头带与项目一致的 transform 过渡", () => {
    const { container } = renderSelect()
    const trigger = container.querySelector('[data-slot="select-trigger"]')
    expect(trigger).not.toBeNull()

    const icon = trigger?.querySelector("svg")
    expect(icon).not.toBeNull()

    const className = icon?.getAttribute("class") ?? ""
    expect(className).toContain("transition-transform")
    expect(className).toContain("duration-200")
    expect(className).toContain("ease-out")
  })

  it("箭头默认不旋转（旋转交给 data-popup-open 规则）", () => {
    renderSelect()
    const icon = screen
      .getByLabelText("测试选择")
      .querySelector("svg") as SVGElement
    expect(icon.className.baseVal ?? icon.getAttribute("class")).not.toContain(
      "rotate-180"
    )
  })
})
