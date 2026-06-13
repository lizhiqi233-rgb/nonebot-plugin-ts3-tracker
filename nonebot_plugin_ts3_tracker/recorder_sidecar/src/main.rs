use std::path::PathBuf;
use std::time::Duration;

use anyhow::{bail, Context, Result};
use clap::Parser;
use futures::prelude::*;
use hound::{SampleFormat, WavSpec, WavWriter};
use slog::Logger;
use tokio::time;
use tracing::{error, info, warn};
use tsclientlib::audio::AudioHandler;
use tsclientlib::prelude::*;
use tsclientlib::{ChannelId, ClientId, Connection, DisconnectOptions, Identity, StreamItem};
use tsproto_packets::packets::AudioData;

const SAMPLE_RATE: u32 = 48_000;
const FRAME_MS: u64 = 20;
const CHANNELS: usize = 2;
const FRAME_SAMPLES: usize = SAMPLE_RATE as usize / (1000 / FRAME_MS as usize) * CHANNELS;

#[derive(Parser, Debug)]
#[command(about = "Record mixed TeamSpeak channel audio to WAV")]
struct Args {
    #[arg(long)]
    host: String,
    #[arg(long, default_value_t = 9987)]
    port: u16,
    #[arg(long)]
    channel_id: u64,
    #[arg(long, default_value = "")]
    channel_name: String,
    #[arg(long, help = "Identity file path or inline identity string")]
    identity: String,
    #[arg(long)]
    nickname: String,
    #[arg(long, default_value = "")]
    password: String,
    #[arg(long, default_value = "")]
    channel_password: String,
    #[arg(long)]
    output: PathBuf,
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();

    let args = Args::parse();
    if args.output.exists() {
        bail!("output file already exists: {}", args.output.display());
    }
    if let Some(parent) = args.output.parent() {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("create output directory {}", parent.display()))?;
    }

    let identity = load_identity(&args.identity)?;
    let address = format!("{}:{}", args.host, args.port);
    let nickname = args.nickname.clone();
    let mut builder = Connection::build(address)
        .identity(identity)
        .name(nickname)
        .channel_id(ChannelId(args.channel_id))
        .input_muted(true)
        .input_hardware_enabled(false)
        .output_hardware_enabled(true);
    if !args.password.is_empty() {
        builder = builder.password(args.password.clone());
    }
    if !args.channel_password.is_empty() {
        builder = builder.channel_password(args.channel_password.clone());
    }

    let mut connection = builder.connect()?;
    connection
        .events()
        .try_filter(|event| future::ready(matches!(event, StreamItem::BookEvents(_))))
        .next()
        .await
        .transpose()?
        .context("failed while waiting for initial book events")?;

    {
        let state = connection
            .get_state()
            .context("connection state unavailable after connect")?;
        state
            .client_update()
            .set_input_muted(true)
            .set_output_muted(false)
            .send(&mut connection)
            .context("failed to configure recorder client audio flags")?;
    }

    eprintln!("READY channel_id={} output={}", args.channel_id, args.output.display());
    info!(
        channel_id = args.channel_id,
        channel_name = %args.channel_name,
        output = %args.output.display(),
        "recorder connected"
    );

    let spec = WavSpec {
        channels: 1,
        sample_rate: SAMPLE_RATE,
        bits_per_sample: 16,
        sample_format: SampleFormat::Int,
    };
    let mut wav_writer =
        WavWriter::create(&args.output, spec).context("create wav writer")?;

    let mut audio_handler = AudioHandler::new(Logger::root(slog::Discard, slog::o!()));
    let mut frame = vec![0.0f32; FRAME_SAMPLES];
    let mut interval = time::interval(Duration::from_millis(FRAME_MS));
    interval.set_missed_tick_behavior(time::MissedTickBehavior::Skip);

    let mut events = connection.events();
    loop {
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {
                info!("received shutdown signal");
                break;
            }
            _ = interval.tick() => {
                frame.fill(0.0);
                audio_handler.fill_buffer(&mut frame);
                write_mono_samples(&mut wav_writer, &frame)?;
            }
            item = events.next() => {
                match item {
                    Some(Ok(StreamItem::Audio(packet))) => {
                        if let Some(from) = audio_sender_id(&packet) {
                            if let Err(error) = audio_handler.handle_packet(from, packet) {
                                warn!(%error, "failed to handle audio packet");
                            }
                        }
                    }
                    Some(Ok(_)) => {}
                    Some(Err(error)) => {
                        error!(%error, "connection event error");
                        break;
                    }
                    None => {
                        info!("connection closed");
                        break;
                    }
                }
            }
        }
    }

    wav_writer
        .finalize()
        .context("finalize wav writer")?;
    drop(events);
    connection.disconnect(DisconnectOptions::new())?;

    eprintln!(
        "DONE channel_id={} output={}",
        args.channel_id,
        args.output.display()
    );
    Ok(())
}

fn write_mono_samples(
    wav_writer: &mut WavWriter<std::fs::File>,
    stereo_frame: &[f32],
) -> Result<()> {
    for chunk in stereo_frame.chunks_exact(CHANNELS) {
        let mono = (chunk[0] + chunk[1]) * 0.5;
        let clipped = mono.clamp(-1.0, 1.0);
        let pcm = (clipped * i16::MAX as f32) as i16;
        wav_writer.write_sample(pcm)?;
    }
    Ok(())
}

fn audio_sender_id(packet: &tsproto_packets::packets::InAudioBuf) -> Option<ClientId> {
    match packet.data().data() {
        AudioData::S2C { from, .. } | AudioData::S2CWhisper { from, .. } => Some(ClientId(*from)),
        _ => None,
    }
}

fn load_identity(raw: &str) -> Result<Identity> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        bail!("identity must not be empty");
    }

    let identity_data = if std::path::Path::new(trimmed).exists() {
        std::fs::read_to_string(trimmed)
            .with_context(|| format!("read identity file {trimmed}"))?
    } else {
        trimmed.to_owned()
    };

    Identity::new_from_str(identity_data.trim()).context("parse TeamSpeak identity")
}
