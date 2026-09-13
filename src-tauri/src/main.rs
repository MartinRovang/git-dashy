fn main() {
    // ponytail: WebKitGTK's GStreamer picks NVIDIA's CUDA decoders on hybrid laptops, they cannot get a
    // CUDA-capable GL context inside the web process, and every <video> dies before its first byte
    // (readyState stays 0, no error). Rank them below the software decoders. Unknown names are ignored,
    // and a value the user set already wins.
    #[cfg(target_os = "linux")]
    if std::env::var_os("GST_PLUGIN_FEATURE_RANK").is_none() {
        std::env::set_var("GST_PLUGIN_FEATURE_RANK", "nvvp8dec:0,nvvp9dec:0,nvh264dec:0,nvh265dec:0,nvav1dec:0");
    }
    std::process::exit(gitdashy::cli::run(std::env::args().skip(1).collect()));
}
