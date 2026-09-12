fn main() {
    std::process::exit(gitdashy::cli::run(std::env::args().skip(1).collect()));
}
