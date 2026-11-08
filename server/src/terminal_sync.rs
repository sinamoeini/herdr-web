//! A bounded screen snapshot for the pinned Herdr 0.9.5 ANSI stream.

use std::collections::BTreeMap;

const FRAME_END: &[u8] = b"\x1b[?2026l";
const FRAME_LIMIT: usize = 8 * 1024 * 1024;

pub struct Screen {
    parser: vt100::Parser,
    modes: Modes,
    pending: Vec<u8>,
    readiness: crate::terminal_probe::AttachmentReadiness,
    pub ready: bool,
}
impl Screen {
    pub fn new() -> Self {
        Self { parser: vt100::Parser::new(24.try_into().unwrap(), 80.try_into().unwrap(), 0), modes: Modes::default(), pending: Vec::new(), readiness: Default::default(), ready: false }
    }
    pub fn resize(&mut self, cols: u16, rows: u16) {
        self.parser.set_size(rows.try_into().unwrap(), cols.try_into().unwrap());
    }
    // Commit only complete render frames. A join between UTF-8 bytes, ANSI
    // sequences, or cell patches sees the previous complete screen and then
    // exactly the same next frame as every existing viewer.
    pub fn push(&mut self, bytes: &[u8]) -> Result<Option<Vec<u8>>, &'static str> {
        if self.pending.len() + bytes.len() > FRAME_LIMIT { return Err("Terminal frame exceeds 8 MiB"); }
        let search = self.pending.len().saturating_sub(FRAME_END.len() - 1);
        self.pending.extend_from_slice(bytes);
        let Some(offset) = self.pending[search..].windows(FRAME_END.len()).rposition(|part| part == FRAME_END) else { return Ok(None); };
        let suffix = self.pending.split_off(search + offset + FRAME_END.len());
        let complete = std::mem::replace(&mut self.pending, suffix);
        self.parser.process(&complete);
        self.modes.push(&complete);
        self.ready |= self.readiness.push(&complete);
        Ok(Some(complete))
    }
    pub fn snapshot(&self) -> Vec<u8> {
        [b"\x1bc".as_slice(), self.parser.screen().state_formatted().as_bytes(), &self.modes.seed()].concat()
    }
}

/// Remember input modes and cursor style independently of the repaint.
/// OSC/DCS strings are opaque, and neither their contents nor arbitrary output
/// are retained. This remains bounded for an indefinitely running attachment.
#[derive(Default)]
pub struct Modes {
    state: u8,
    csi: Vec<u8>,
    modes: BTreeMap<u16, bool>,
    cursor: Vec<u8>,
}

impl Modes {
    pub fn push(&mut self, bytes: &[u8]) {
        for &byte in bytes {
            match self.state {
                0 => if byte == 0x1b { self.state = 1; },
                1 => match byte {
                    b'[' => { self.csi.clear(); self.state = 2; }
                    b']' | b'P' | b'_' | b'^' => self.state = 3,
                    0x1b => (),
                    _ => self.state = 0,
                },
                2 => {
                    if (0x40..=0x7e).contains(&byte) {
                        if matches!(byte, b'h' | b'l') && self.csi.first() == Some(&b'?') {
                            if let Ok(text) = std::str::from_utf8(&self.csi[1..]) {
                                for mode in text.split(';').filter_map(|part| part.parse::<u16>().ok()) {
                                    if matches!(mode, 1 | 7 | 25 | 1000 | 1002 | 1003 | 1004 | 1005 | 1006 | 1007 | 1015 | 1016 | 1049 | 2004) {
                                        self.modes.insert(mode, byte == b'h');
                                    }
                                }
                            }
                        } else if byte == b'q' && self.csi.last() == Some(&b' ') {
                            self.cursor = [b"\x1b[", self.csi.as_slice(), &[byte]].concat();
                        }
                        self.state = 0;
                    } else if byte == 0x1b {
                        self.state = 1;
                    } else if self.csi.len() < 64 {
                        self.csi.push(byte);
                    } else {
                        self.state = 0;
                    }
                }
                3 => match byte { 7 => self.state = 0, 0x1b => self.state = 4, _ => () },
                4 => self.state = if byte == b'\\' { 0 } else if byte == 0x1b { 4 } else { 3 },
                _ => unreachable!(),
            }
        }
    }

    pub fn seed(&self) -> Vec<u8> {
        // Herdr disables wrapping because its renderer positions every cell.
        let mut bytes = b"\x1b[?7l".to_vec();
        for (mode, enabled) in &self.modes {
            bytes.extend_from_slice(format!("\x1b[?{mode}{}", if *enabled { 'h' } else { 'l' }).as_bytes());
        }
        bytes.extend_from_slice(&self.cursor);
        bytes
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn snapshots_rebuild_patches_and_preserve_utf8_across_every_fragmentation() {
        let first = b"\x1b[?7l\x1b[?2004h\x1b[?2026h\x1b[2J\x1b[1;1HOLD SCREEN\x1b[?2026l";
        let patch = "\x1b[?2026h\x1b[1;1H猫 new\x1b[?2026l".as_bytes();
        for chunk in 1..=patch.len() {
            let mut screen = Screen::new();
            screen.push(first).unwrap();
            let mut reference = vt100::Parser::new(24.try_into().unwrap(), 80.try_into().unwrap(), 0);
            reference.process(first);
            reference.process(patch);
            for bytes in patch.chunks(chunk) { screen.push(bytes).unwrap(); }
            let mut viewer = vt100::Parser::new(24.try_into().unwrap(), 80.try_into().unwrap(), 0);
            viewer.process(&screen.snapshot());
            assert_eq!(viewer.screen().contents(), reference.screen().contents());
            assert_eq!(viewer.screen().cursor_position(), reference.screen().cursor_position());
            assert!(viewer.screen().bracketed_paste());
        }
    }

    #[test]
    fn incomplete_frames_do_not_change_join_snapshot_and_buffer_is_bounded() {
        let mut screen = Screen::new();
        screen.push(b"\x1b[?2026hready\x1b[?2026l").unwrap();
        let before = screen.snapshot();
        assert!(screen.push(b"\x1b[?2026hpartial").unwrap().is_none());
        assert_eq!(screen.snapshot(), before);
        assert!(screen.push(&vec![b'x'; FRAME_LIMIT]).is_err());
    }

    #[test]
    fn modes_survive_fragmentation_without_replaying_osc_or_old_screen_contents() {
        let input = b"\x1b[?2004h\x1b[?1002;1006h\x1b[?1002l\x1b[5 q\x1b]8;;https://example.test/\x1b[?1003h\x1b\\private text";
        for chunk in 1..=input.len() {
            let mut modes = Modes::default();
            for bytes in input.chunks(chunk) { modes.push(bytes); }
            assert_eq!(modes.seed(), b"\x1b[?7l\x1b[?1002l\x1b[?1006h\x1b[?2004h\x1b[5 q");
        }
    }
}
