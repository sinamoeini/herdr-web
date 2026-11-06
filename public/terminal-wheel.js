// The attached Herdr client renders in xterm's alternate buffer. Its history
// lives on the backend, so xterm's fallback arrow keys cannot scroll that history.
export class TerminalWheel {
  constructor(terminal, ready, send) {
    this.terminal = terminal;
    this.ready = ready;
    this.send = send;
    this.reset();
  }

  reset() {
    this.remainder = 0;
    this.lastEvent = 0;
  }

  handle(event) {
    if (!this.ready() || event.ctrlKey || event.metaKey) {
      this.reset();
      return false;
    }
    const terminal = this.terminal;
    // Keep xterm's negotiated mouse encoding for applications that request it.
    if (terminal.modes.mouseTrackingMode !== "none" || terminal.buffer.active.type !== "alternate") {
      this.reset();
      return true;
    }
    if (!event.deltaY || Math.abs(event.deltaX) > Math.abs(event.deltaY)) return false;
    event.preventDefault();
    event.stopPropagation();
    const bounds = terminal.element.querySelector(".xterm-screen")?.getBoundingClientRect();
    if (!bounds?.width || !bounds.height) return false;

    let delta = event.deltaY;
    // Match xterm's wheel threshold, including accumulation for small trackpad
    // movements. Herdr applies its configured scroll distance to each report.
    if (event.deltaMode === 0) {
      delta /= bounds.height / terminal.rows;
      if (Math.abs(event.deltaY) < 50) delta *= 0.3;
    } else if (event.deltaMode === 2) {
      delta *= terminal.rows;
    }
    if (event.timeStamp - this.lastEvent > 250 || Math.sign(delta) !== Math.sign(this.remainder)) this.remainder = 0;
    this.lastEvent = event.timeStamp;
    this.remainder += delta;
    if (Math.abs(this.remainder) < 1) return false;
    this.remainder %= 1;

    const column = Math.max(1, Math.min(terminal.cols, 1 + Math.floor((event.clientX - bounds.left) * terminal.cols / bounds.width)));
    const row = Math.max(1, Math.min(terminal.rows, 1 + Math.floor((event.clientY - bounds.top) * terminal.rows / bounds.height)));
    const button = (delta < 0 ? 64 : 65) + (event.shiftKey ? 4 : 0) + (event.altKey ? 8 : 0);
    // Herdr parses this SGR wheel report even with ui.mouse_capture=false, then
    // routes it to host history or the foreground application as appropriate.
    this.send(`\x1b[<${button};${column};${row}M`);
    return false;
  }
}
