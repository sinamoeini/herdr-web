pub const QUERY: &[u8] = b"\x1b[?1016$p\x1b[c";
pub const REPLY: &[u8] = b"\x1b[?1016;0$y\x1b[?1;2c";

const FRAME_BEGIN: &[u8] = b"\x1b[?2026h";
const FRAME_END: &[u8] = b"\x1b[?2026l";

#[derive(Default)]
pub struct AttachmentReadiness {
    matched: usize,
    started: bool,
    ready: bool,
}

impl AttachmentReadiness {
    pub fn push(&mut self, bytes: &[u8]) -> bool {
        if self.ready { return false; }
        for byte in bytes {
            let pattern = if self.started { FRAME_END } else { FRAME_BEGIN };
            self.matched = if *byte == pattern[self.matched] {
                self.matched + 1
            } else {
                usize::from(*byte == pattern[0])
            };
            if self.matched == pattern.len() {
                self.matched = 0;
                if self.started {
                    self.ready = true;
                    return true;
                }
                self.started = true;
            }
        }
        false
    }
}

#[derive(Default)]
pub struct CapabilityFilter {
    pending: Vec<u8>,
    answered: bool,
}

impl CapabilityFilter {
    pub fn push(&mut self, bytes: &[u8]) -> (Vec<u8>, bool) {
        if self.answered { return (bytes.to_vec(), false); }
        self.pending.extend_from_slice(bytes);
        if let Some(offset) = self.pending.windows(QUERY.len()).position(|window| window == QUERY) {
            let mut output = self.pending[..offset].to_vec();
            output.extend_from_slice(&self.pending[offset + QUERY.len()..]);
            self.pending.clear();
            self.answered = true;
            return (output, true);
        }
        let retained = (1..QUERY.len()).rev()
            .find(|length| self.pending.ends_with(&QUERY[..*length])).unwrap_or(0);
        let emitted = self.pending.len() - retained;
        let suffix = self.pending.split_off(emitted);
        (std::mem::replace(&mut self.pending, suffix), false)
    }

    pub fn finish(&mut self) -> Vec<u8> { std::mem::take(&mut self.pending) }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn readiness_requires_a_complete_frame_across_every_fragmentation() {
        let bytes = [b"\x1b[?2004h\x1b[?7l".as_slice(), FRAME_BEGIN, b"\x1b[?25lprompt", FRAME_END].concat();
        for split in 0..bytes.len() {
            let mut readiness = AttachmentReadiness::default();
            assert!(!readiness.push(&bytes[..split]));
            assert!(readiness.push(&bytes[split..]));
            assert!(!readiness.push(&bytes));
        }
        let mut readiness = AttachmentReadiness::default();
        for (index, byte) in bytes.iter().enumerate() {
            assert_eq!(readiness.push(&[*byte]), index + 1 == bytes.len());
        }
    }

    #[test]
    fn capability_probe_and_busy_failure_never_report_ready() {
        let bytes = [QUERY, b"\x1b[?2004hterminal attach failed: already has an attached client\r\n", FRAME_END].concat();
        let mut readiness = AttachmentReadiness::default();
        for byte in bytes { assert!(!readiness.push(&[byte])); }
    }

    #[test]
    fn incomplete_frames_wait_for_their_end_despite_unrelated_prefixes() {
        let mut readiness = AttachmentReadiness::default();
        assert!(!readiness.push(FRAME_END));
        assert!(!readiness.push(b"\x1b[?2026X\x1b\x1b[?2026"));
        assert!(!readiness.push(b"hpartial frame\x1b[?2026"));
        assert!(readiness.push(b"l"));
        assert!(!readiness.push(FRAME_END));
    }

    #[test]
    fn every_fragmentation_including_single_bytes() {
        for split in 0..=QUERY.len() {
            let mut filter = CapabilityFilter::default();
            let (first, first_reply) = filter.push(&QUERY[..split]);
            let (second, second_reply) = filter.push(&QUERY[split..]);
            assert!(first.is_empty() && second.is_empty());
            assert_ne!(first_reply, second_reply);
            assert!(filter.finish().is_empty());
        }
        let mut filter = CapabilityFilter::default();
        let mut replies = 0;
        for byte in QUERY {
            let (output, reply) = filter.push(&[*byte]);
            assert!(output.is_empty());
            replies += usize::from(reply);
        }
        assert_eq!(replies, 1);
    }

    #[test]
    fn every_incomplete_eof_is_preserved() {
        for length in 0..QUERY.len() {
            let mut filter = CapabilityFilter::default();
            let (mut output, reply) = filter.push(&QUERY[..length]);
            output.extend(filter.finish());
            assert!(!reply);
            assert_eq!(output, QUERY[..length]);
        }
    }

    #[test]
    fn only_first_exact_query_changes() {
        let prefix = "猫 café\x1b[31m ordinary \x1b[?1016;2$y".as_bytes();
        let input = [prefix, REPLY, QUERY, b"prompt", QUERY, REPLY].concat();
        let expected = [prefix, REPLY, b"prompt", QUERY, REPLY].concat();
        for chunk_size in 1..input.len() {
            let mut filter = CapabilityFilter::default();
            let mut actual = Vec::new();
            let mut replies = 0;
            for chunk in input.chunks(chunk_size) {
                let (output, reply) = filter.push(chunk);
                actual.extend(output);
                replies += usize::from(reply);
            }
            actual.extend(filter.finish());
            assert_eq!(actual, expected);
            assert_eq!(replies, 1);
        }
    }

    #[test]
    fn false_prefix_and_overlapping_escape_are_preserved() {
        let input = [b"\x1b[?1016$X\x1b".as_slice(), QUERY, b"hello"].concat();
        let mut filter = CapabilityFilter::default();
        let (output, reply) = filter.push(&input);
        assert!(reply);
        assert_eq!(output, b"\x1b[?1016$X\x1bhello");
    }
}
