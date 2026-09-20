//! A stand-in for the GitHub API, for tests that need one call to land and the next to be refused.
//!
//! ponytail: the suite could already make every call fail — point `$GITHUB_API` at a closed port —
//! and that is enough for "the whole thing is unreachable". It is not enough for the failures that
//! actually bite: the hello lands and the review is refused, or the diff is served and the head has
//! moved under it. Those need two answers to two calls, which is this.
//!
//! ponytail: routes match the Accept header as well as the path, because `GET /pulls/{n}` is two
//! different endpoints. `github::diff` asks it for a diff and `github::head_sha` asks it for JSON,
//! and a test of the second has to be able to serve the first.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

/// What a route answers with.
pub struct Reply {
    pub status: u16,
    pub body: String,
}

impl Reply {
    pub fn new(status: u16, body: &str) -> Self {
        Reply {
            status,
            body: body.into(),
        }
    }
}

struct Route {
    /// Matched as a substring of the request target, so a test names the tail it cares about.
    path: String,
    /// When set, the request's Accept header must contain this too.
    accept: Option<String>,
    reply: Reply,
}

/// One request the stand-in was asked for: the method, the target and the Accept header.
#[derive(Clone, Debug)]
pub struct Hit {
    pub method: String,
    pub path: String,
    pub accept: String,
}

/// A running stand-in. Point `$GITHUB_API` at [`Api::url`]; it stops when this is dropped.
pub struct Api {
    stop: Arc<AtomicBool>,
    hits: Arc<Mutex<Vec<Hit>>>,
    thread: Option<std::thread::JoinHandle<()>>,
    port: u16,
}

impl Api {
    /// Start one. Routes are tried in order; the first whose path and Accept match wins.
    pub fn start(routes: Vec<(&str, Option<&str>, Reply)>) -> Api {
        let routes: Vec<Route> = routes
            .into_iter()
            .map(|(path, accept, reply)| Route {
                path: path.into(),
                accept: accept.map(String::from),
                reply,
            })
            .collect();
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind a port");
        let port = listener.local_addr().expect("the bound address").port();
        let stop = Arc::new(AtomicBool::new(false));
        let hits = Arc::new(Mutex::new(Vec::new()));
        let thread = {
            let (stop, hits) = (Arc::clone(&stop), Arc::clone(&hits));
            std::thread::spawn(move || {
                for conn in listener.incoming() {
                    if stop.load(Ordering::SeqCst) {
                        return;
                    }
                    // ponytail: one bad connection must not end the stand-in. A test that fails
                    // mid-request would otherwise take every later request down with it, and the
                    // failure would read as "the server went away" rather than as the assertion.
                    if let Ok(conn) = conn {
                        let _ = serve(conn, &routes, &hits);
                    }
                }
            })
        };
        Api {
            stop,
            hits,
            thread: Some(thread),
            port,
        }
    }

    /// The base URL to put in `$GITHUB_API`.
    pub fn url(&self) -> String {
        format!("http://127.0.0.1:{}", self.port)
    }

    /// Every request it was asked for, in order.
    pub fn hits(&self) -> Vec<Hit> {
        self.hits.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }

    /// Whether any request's target CONTAINS `path`. Routes match the same way, so `/pulls/7` also
    /// answers `/pulls/77` and `/pulls/7/reviews`: name enough of the tail to tell them apart.
    pub fn saw(&self, method: &str, path: &str) -> bool {
        self.hits()
            .iter()
            .any(|h| h.method == method && h.path.contains(path))
    }

    /// Whether the PR itself was read as JSON — `GET /pulls/{n}` without the diff Accept.
    ///
    /// ponytail: `saw("GET", "/pulls/7")` cannot tell the two apart, because fetching the diff is a
    /// GET on that same path. An assertion that cannot fail is worse than none: it reads as coverage.
    pub fn read_pr(&self, number: u64) -> bool {
        let tail = format!("/pulls/{number}");
        self.hits()
            .iter()
            .any(|h| h.method == "GET" && h.path.ends_with(&tail) && !h.accept.contains("diff"))
    }
}

impl Drop for Api {
    fn drop(&mut self) {
        // ponytail: the flag alone does not end it — the thread is parked inside accept() and only
        // looks at the flag when a connection wakes it. One throwaway connection is that wake-up.
        self.stop.store(true, Ordering::SeqCst);
        let _ = TcpStream::connect(("127.0.0.1", self.port));
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}

fn serve(mut conn: TcpStream, routes: &[Route], hits: &Mutex<Vec<Hit>>) -> std::io::Result<()> {
    // ponytail: a deadline, because Drop joins this thread. A client that connects and never sends a
    // request line would park serve() for ever, and the test would HANG rather than fail — the one
    // failure a test cannot report on itself.
    conn.set_read_timeout(Some(std::time::Duration::from_secs(5)))?;
    let mut reader = BufReader::new(conn.try_clone()?);
    let mut start = String::new();
    if reader.read_line(&mut start)? == 0 {
        return Ok(()); // the wake-up connection from Drop
    }
    let mut parts = start.split_whitespace();
    let method = parts.next().unwrap_or("").to_string();
    let path = parts.next().unwrap_or("").to_string();

    let (mut accept, mut len) = (String::new(), 0usize);
    loop {
        let mut line = String::new();
        if reader.read_line(&mut line)? == 0 || line == "\r\n" || line == "\n" {
            break;
        }
        let lower = line.to_ascii_lowercase();
        if let Some(v) = lower.strip_prefix("accept:") {
            accept = v.trim().to_string();
        } else if let Some(v) = lower.strip_prefix("content-length:") {
            len = v.trim().parse().unwrap_or(0);
        }
    }
    // ponytail: the body is READ even though nothing looks at it. Answering before the client has
    // finished writing gets the response thrown away and the call reported as a broken pipe, which
    // in a test reads as the request having failed for a reason the test did not arrange.
    if len > 0 {
        let mut body = vec![0u8; len];
        reader.read_exact(&mut body)?;
    }

    hits.lock().unwrap_or_else(|e| e.into_inner()).push(Hit {
        method: method.clone(),
        path: path.clone(),
        accept: accept.clone(),
    });

    let hit = routes.iter().find(|r| {
        path.contains(&r.path)
            && r.accept
                .as_ref()
                .is_none_or(|a| accept.contains(&a.to_ascii_lowercase()))
    });
    let (status, body) = match hit {
        Some(r) => (r.reply.status, r.reply.body.clone()),
        // ponytail: an unrouted path is a 404 with a message naming it, not a hang or a panic on a
        // worker thread. A test that forgot a route should fail saying which one.
        None => (404, format!("{{\"message\":\"no route for {path}\"}}")),
    };
    write!(
        conn,
        "HTTP/1.1 {status} {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        if (200..300).contains(&status) { "OK" } else { "Error" },
        body.len()
    )?;
    conn.flush()
}

/// Points `$GITHUB_API` at a stand-in and puts the environment back when it drops.
///
/// ponytail: restored from Drop and not at the end of a test body, so a panicking test cannot leave
/// the variable pointing at a port nobody is listening on. This suite shares one process and team's
/// git calls read the same environment, where a leak surfaces as `sync: git failed` somewhere else
/// entirely (#160).
/// ponytail: the WHOLE config is put back, not the two fields a caller happens to change. A test
/// pointing at the stand-in has to turn `demo` off, and usually one more switch besides; restoring
/// a named list means the next switch someone reaches for is the one that leaks.
///
/// Hold `config::test_lock()` for as long as this lives: both the config and the environment are
/// process-global, and the suite runs in parallel.
pub struct Pointed {
    was: Option<String>,
    config: crate::config::Config,
}

impl Pointed {
    pub fn at(api: &Api) -> Pointed {
        let p = Pointed {
            was: std::env::var("GITHUB_API").ok(),
            config: crate::config::get(),
        };
        std::env::set_var("GITHUB_API", api.url());
        crate::config::update(|c| c.demo = false);
        p
    }
}

impl Drop for Pointed {
    fn drop(&mut self) {
        match self.was.take() {
            Some(v) => std::env::set_var("GITHUB_API", v),
            None => std::env::remove_var("GITHUB_API"),
        }
        let saved = self.config.clone();
        crate::config::update(|c| *c = saved);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn it_answers_each_route_and_records_what_it_was_asked() {
        // ponytail: the lock FIRST, then Pointed, and the environment goes back from its Drop. This
        // test's own first draft restored it at the end of the body and took three unrelated tests
        // down with it — the very failure #160 is about, reproduced by the helper written to
        // investigate it.
        let _g = crate::config::test_lock();
        let api = Api::start(vec![
            ("/pulls/77", Some("diff"), Reply::new(200, "diff --git a/x b/x")),
            ("/pulls/77", None, Reply::new(200, r#"{"head":{"sha":"abc"}}"#)),
        ]);
        let _p = Pointed::at(&api);

        // the same path, told apart by what the caller will accept
        let d = crate::github::diff("acme/stub", 77).unwrap();
        assert!(d.starts_with("diff --git"));
        assert_eq!(crate::github::head_sha("acme/stub", 77), "abc");
        // and a path with no route says so rather than hanging
        assert!(crate::github::api("/repos/acme/stub/pulls/99", 5).is_err());

        assert!(api.saw("GET", "/pulls/77"));
        assert!(api.read_pr(77), "the JSON read is told apart from the diff one");
        assert_eq!(api.hits().len(), 3);
    }

    #[test]
    fn a_route_whose_accept_does_not_match_is_not_served() {
        let _g = crate::config::test_lock();
        // only a diff is on offer here
        let api = Api::start(vec![(
            "/pulls/78",
            Some("diff"),
            Reply::new(200, "diff --git a/x b/x"),
        )]);
        let _p = Pointed::at(&api);

        assert!(crate::github::diff("acme/stub", 78).is_ok());
        // asking the same path for JSON falls through to the 404 rather than being handed the diff
        assert_eq!(crate::github::head_sha("acme/stub", 78), "");
        assert!(api.read_pr(78), "it was asked, and refused");
    }
}
