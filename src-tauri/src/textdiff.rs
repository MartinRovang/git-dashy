//! difflib for Rust: SequenceMatcher.ratio() over tokens, and a unified diff for the dream viewer.
//! ponytail: memory.NEAR was tuned against Python's ratio, so this is that algorithm (longest matching
//! block recursion, no autojunk), not a Myers diff with a different number on the same inputs.

/// (start in a, start in b, length) of one matching run.
type Block = (usize, usize, usize);

/// The longest run a[alo..ahi] and b[blo..bhi] share. Ties: earliest in a, then earliest in b, which is
/// the order difflib.find_longest_match visits them in (i ascending, then j ascending, strict `>`).
fn longest_match<T: PartialEq>(a: &[T], b: &[T], alo: usize, ahi: usize, blo: usize, bhi: usize) -> Block {
    let (mut besti, mut bestj, mut bestsize) = (alo, blo, 0);
    // j2len[j - blo] = length of the run ending at a[i - 1], b[j - 1]; rebuilt per i like difflib.
    let mut j2len = vec![0usize; bhi - blo];
    for (i, ai) in a.iter().enumerate().take(ahi).skip(alo) {
        let mut newj2len = vec![0usize; bhi - blo];
        for (j, bj) in b.iter().enumerate().take(bhi).skip(blo) {
            if ai == bj {
                let k = if j > blo { j2len[j - blo - 1] } else { 0 } + 1;
                newj2len[j - blo] = k;
                if k > bestsize {
                    besti = i + 1 - k;
                    bestj = j + 1 - k;
                    bestsize = k;
                }
            }
        }
        j2len = newj2len;
    }
    (besti, bestj, bestsize)
}

/// Every matching block, sorted, adjacent runs merged, ending with the (len a, len b, 0) sentinel.
fn matching_blocks<T: PartialEq>(a: &[T], b: &[T]) -> Vec<Block> {
    let (la, lb) = (a.len(), b.len());
    let mut queue = vec![(0, la, 0, lb)];
    let mut blocks = Vec::new();
    while let Some((alo, ahi, blo, bhi)) = queue.pop() {
        let (i, j, k) = longest_match(a, b, alo, ahi, blo, bhi);
        if k > 0 {
            blocks.push((i, j, k));
            if alo < i && blo < j {
                queue.push((alo, i, blo, j));
            }
            if i + k < ahi && j + k < bhi {
                queue.push((i + k, ahi, j + k, bhi));
            }
        }
    }
    blocks.sort_unstable();
    let mut out: Vec<Block> = Vec::with_capacity(blocks.len() + 1);
    for (i, j, k) in blocks {
        match out.last_mut() {
            Some(last) if last.0 + last.2 == i && last.1 + last.2 == j => last.2 += k,
            _ => out.push((i, j, k)),
        }
    }
    out.push((la, lb, 0));
    out
}

/// 2*M/T over two token sequences, like difflib.SequenceMatcher(None, a, b).ratio().
pub fn ratio<T: PartialEq>(a: &[T], b: &[T]) -> f64 {
    let total = a.len() + b.len();
    if total == 0 {
        return 1.0;
    }
    let matches: usize = matching_blocks(a, b).iter().map(|b| b.2).sum();
    2.0 * matches as f64 / total as f64
}

/// One edit: "equal" | "replace" | "delete" | "insert" over a[i1..i2] and b[j1..j2].
#[derive(Clone, Copy)]
struct Op {
    tag: &'static str,
    i1: usize,
    i2: usize,
    j1: usize,
    j2: usize,
}

fn opcodes<T: PartialEq>(a: &[T], b: &[T]) -> Vec<Op> {
    let (mut i, mut j) = (0, 0);
    let mut out = Vec::new();
    for (ai, bj, size) in matching_blocks(a, b) {
        let tag = match (i < ai, j < bj) {
            (true, true) => "replace",
            (true, false) => "delete",
            (false, true) => "insert",
            (false, false) => "",
        };
        if !tag.is_empty() {
            out.push(Op {
                tag,
                i1: i,
                i2: ai,
                j1: j,
                j2: bj,
            });
        }
        i = ai + size;
        j = bj + size;
        if size > 0 {
            out.push(Op {
                tag: "equal",
                i1: ai,
                i2: i,
                j1: bj,
                j2: j,
            });
        }
    }
    out
}

/// difflib.get_grouped_opcodes: hunks of edits with `n` lines of context, split where an equal run
/// exceeds 2n lines.
fn grouped<T: PartialEq>(a: &[T], b: &[T], n: usize) -> Vec<Vec<Op>> {
    let mut codes = opcodes(a, b);
    if codes.is_empty() {
        codes.push(Op {
            tag: "equal",
            i1: 0,
            i2: 1,
            j1: 0,
            j2: 1,
        });
    }
    if let Some(c) = codes.first_mut().filter(|c| c.tag == "equal") {
        c.i1 = c.i1.max(c.i2.saturating_sub(n));
        c.j1 = c.j1.max(c.j2.saturating_sub(n));
    }
    if let Some(c) = codes.last_mut().filter(|c| c.tag == "equal") {
        c.i2 = c.i2.min(c.i1 + n);
        c.j2 = c.j2.min(c.j1 + n);
    }
    let mut groups = Vec::new();
    let mut group: Vec<Op> = Vec::new();
    for mut c in codes {
        if c.tag == "equal" && c.i2 - c.i1 > 2 * n {
            group.push(Op {
                i2: c.i2.min(c.i1 + n),
                j2: c.j2.min(c.j1 + n),
                ..c
            });
            groups.push(std::mem::take(&mut group));
            c.i1 = c.i1.max(c.i2 - n);
            c.j1 = c.j1.max(c.j2 - n);
        }
        group.push(c);
    }
    if !(group.is_empty() || group.len() == 1 && group[0].tag == "equal") {
        groups.push(group);
    }
    groups
}

/// "12" for one line, "12,3" for a run, "11,0" for an empty range: difflib._format_range_unified.
fn range(start: usize, stop: usize) -> String {
    let length = stop - start;
    match length {
        1 => format!("{}", start + 1),
        0 => format!("{},0", start),
        _ => format!("{},{}", start + 1, length),
    }
}

/// A unified diff of two texts with `name` in the headers, like difflib.unified_diff.
pub fn unified(name: &str, before: &str, after: &str) -> String {
    let a: Vec<&str> = before.lines().collect();
    let b: Vec<&str> = after.lines().collect();
    let mut out: Vec<String> = Vec::new();
    for group in grouped(&a, &b, 3) {
        if out.is_empty() {
            out.push(format!("--- {name}"));
            out.push(format!("+++ {name}"));
        }
        let (first, last) = (group[0], group[group.len() - 1]);
        out.push(format!(
            "@@ -{} +{} @@",
            range(first.i1, last.i2),
            range(first.j1, last.j2)
        ));
        for c in group {
            if c.tag == "equal" {
                out.extend(a[c.i1..c.i2].iter().map(|l| format!(" {l}")));
                continue;
            }
            if c.tag != "insert" {
                out.extend(a[c.i1..c.i2].iter().map(|l| format!("-{l}")));
            }
            if c.tag != "delete" {
                out.extend(b[c.j1..c.j2].iter().map(|l| format!("+{l}")));
            }
        }
    }
    out.join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn close(a: f64, b: f64) -> bool {
        (a - b).abs() < 1e-4
    }

    #[test]
    fn ratio_matches_python_values() {
        assert!(close(ratio(&["a", "b", "c"], &["a", "b", "d"]), 0.6667));
        assert!(close(ratio(&["a", "b", "c"], &["a", "b", "c"]), 1.0));
        assert!(close(ratio(&["a", "b"], &["c", "d"]), 0.0));
        assert!(close(ratio::<&str>(&[], &[]), 1.0));
        assert!(close(ratio(&["a"], &[]), 0.0));
        // SequenceMatcher(None, "abxcd", "abcd").ratio() == 0.888...
        let a: Vec<char> = "abxcd".chars().collect();
        let b: Vec<char> = "abcd".chars().collect();
        assert!(close(ratio(&a, &b), 8.0 / 9.0));
        // difflib's "private thoughts" / "private houghts" style: repeated runs after the first block
        let a: Vec<char> = "the quick brown fox".chars().collect();
        let b: Vec<char> = "the quick red fox".chars().collect();
        assert!(close(ratio(&a, &b), 2.0 * 15.0 / 36.0));
    }

    #[test]
    fn longest_match_breaks_ties_earliest_in_a_then_b() {
        let a = ["x", "a", "y", "a"];
        let b = ["a", "z", "a"];
        assert_eq!(longest_match(&a, &b, 0, 4, 0, 3), (1, 0, 1));
    }

    #[test]
    fn unified_is_difflib_shaped() {
        let before = "one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\nnine\nten\n";
        let after = "one\ntwo\nthree\nFOUR\nfive\nsix\nseven\neight\nnine\nten\n";
        assert_eq!(
            unified("f.txt", before, after),
            "--- f.txt\n+++ f.txt\n@@ -1,7 +1,7 @@\n one\n two\n three\n-four\n+FOUR\n five\n six\n seven"
        );
        assert_eq!(unified("f", "a\nb\n", "a\nb\n"), "");
        assert_eq!(unified("f", "", "a\n"), "--- f\n+++ f\n@@ -0,0 +1 @@\n+a");
        assert_eq!(unified("f", "a\n", ""), "--- f\n+++ f\n@@ -1 +0,0 @@\n-a");
    }

    #[test]
    fn unified_splits_hunks_when_the_gap_exceeds_twice_the_context() {
        let before: String = (1..=20).map(|i| format!("l{i}\n")).collect();
        let after = before.replace("l2\n", "L2\n").replace("l19\n", "L19\n");
        let got = unified("f", &before, &after);
        assert_eq!(got.matches("@@").count(), 4, "{got}");
        assert!(got.contains("@@ -1,5 +1,5 @@\n l1\n-l2\n+L2\n l3\n l4\n l5\n@@ -16,5 +16,5 @@\n l16\n l17\n l18\n-l19\n+L19\n l20"));
        // a gap of exactly 2n stays one hunk
        let after = before.replace("l2\n", "L2\n").replace("l9\n", "L9\n");
        assert_eq!(unified("f", &before, &after).matches("@@").count(), 2);
    }
}
