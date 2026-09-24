fn main() {
    gitdashy::shell::gstreamer_env();
    std::process::exit(gitdashy::cli::run(std::env::args().skip(1).collect()));
}
