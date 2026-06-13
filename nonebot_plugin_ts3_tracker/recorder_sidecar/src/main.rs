use std::path::PathBuf;
use std::time::Duration;

use anyhow::{bail, Context, Result};
use clap::Parser;
use futures::prelude::*;
use hound::{SampleFormat, WavSpec, WavWriter};
use slog::Logger;
use tokio::time;
use tracing::{error, info};
use tsclientlib::audio::AudioHandler;
use tsclientlib::prelude::*;
use tsclientlib::{ChannelId, ClientId, Connection, DisconnectOptions, Identity, StreamItem};
use tsproto_packets::packets::AudioData;

const FRAME_SAMPLES: usize = 48000 / 50;

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
    let mut builder = Connection::build(address)
        .identity(identity)
        .name(&args.nickname)
        .channel_id(ChannelId(args.channel_id));
    if !args.password.is_empty() {
        builder = builder.password(&args.password);
    }
    if !args.channel_password.is_empty() {
        builder = builder.channel_password(&args.channel_password);
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
            .set_output_muted(true)
            .send(&mut connection)
            .context("failed to mute recorder client")?;
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
        sample_rate: 48_000,
        bits_per_sample: 16,
        sample_format: SampleFormat::Int,
    };
    let mut wav_writer =
        WavWriter::create(&args.output, spec).context("create wav writer")?;

    let mut audio_handler = AudioHandler::new(Logger::root(slog::Discard, slog::o!()));
    let mut frame = vec![0.0f32; FRAME_SAMPLES];
    let mut interval = time::interval(Duration::from_millis(20));
    interval.set_missed_tick_behavior(time::MissedTickBehavior::Skip);

    let mut events = connection.events();
    loop {
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {
                info!("received shutdown signal");
                break;
            }
            _ = interval.tick() => {
                audio_handler.fill_buffer(&mut frame);
                for sample in &frame {
                    let clipped = sample.clamp(-1.0, 1.0);
                    let pcm = (clipped * i16::MAX as f32) as i16;
                    wav_writer.write_sample(pcm)?;
                }
            }
            item = events.next() => {
                match item {
                    Some(Ok(StreamItem::Audio(packet))) => {
                        if let Some(from) = audio_sender_id(&packet) {
                            let _ = audio_handler.handle_packet(from, packet);
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
    connection.disconnect(DisconnectOptions::new())?;
    let _ = events.next().await;

    eprintln!(
        "DONE channel_id={} output={}",
        args.channel_id,
        args.output.display()
    );
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
