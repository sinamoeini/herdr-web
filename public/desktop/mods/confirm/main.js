// Asks before something destructive, e.g. "Close space?". Opened as a
// dialog with props { title, detail, command: { name, args } }.
// Enter confirms (runs the command), Esc cancels.

import { View, div } from "/desktop/runtime/gpui.js";
import { v_flex, h_flex } from "/desktop/runtime/base.js";
import { run, props, closeOverlay, theme } from "/desktop/runtime/herdr.js";

export default class Confirm extends View {
  init(_props, cx) {
    this.handle = cx.focus_handle();
    this.handle.focus();
  }

  yes() {
    const p = this.webProps;
    closeOverlay(p.overlay);
    run(p.command.name, p.command.args ?? {});
  }

  render(cx) {
    const t = theme();
    const p = this.webProps ?? {};
    const button = (id, label, bg, fg, onClick) =>
      h_flex().id(id).h(32).px(14).items_center().rounded(8).border_1().border_color(id === "no" ? t.input : bg).bg(bg).text_color(fg).cursor_pointer().hover((s) => s.opacity(0.88)).on_click(onClick).child(label);
    return v_flex()
      .id("confirm")
      .track_focus(this.handle)
      .on_key_down((e, cx) => {
        if (e.key === "enter") this.yes();
        else if (e.key === "escape") closeOverlay(this.webProps.overlay);
        else return;
        cx.stop_propagation();
      })
      .size_full()
      .min_h(0)
      .p(18)
      .gap(12)
      .bg(t.popover)
      .text_size(13)
      .child(h_flex().items_center().child(div().flex_1().text_size(15).font_semibold().text_color(t.foreground).child(p.title ?? "Are you sure?")).child(div().text_size(11).text_color(t.dim).child("Esc")))
      .child(div().flex_1().min_h(0).overflow_y_scrollbar().text_color(t.muted).child(p.detail ?? ""))
      .child(
        h_flex()
          .justify_end()
          .gap(8)
          .text_size(12)
          .child(button("no", "Cancel", t.raised, t.foreground, () => closeOverlay(this.webProps.overlay)))
          .child(button("yes", "Confirm", t.solid, t.solid_foreground, () => this.yes())),
      );
  }
}
