#![forbid(unsafe_code)]
//! # cs-ipc
//!
//! The inter-process protocol between the ClaudeStudio SwiftUI front-end and the
//! Rust sidecar.
//!
//! Each message is encoded as a **length-prefixed MessagePack frame**:
//!
//! ```text
//! +-------------------+----------------------------+
//! | u32 big-endian len | rmp-serde body (`len` bytes) |
//! +-------------------+----------------------------+
//! ```
//!
//! The body is the MessagePack serialization of any [`serde::Serialize`] value —
//! in practice a [`cs_types::IpcEnvelope`]. Use [`encode_frame`] / [`decode_frame`]
//! for the in-memory codec, or the async [`FrameReader`] / [`FrameWriter`] to read
//! and write framed messages over any tokio stream.

use serde::{de::DeserializeOwned, Serialize};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};

/// Re-exported so downstream crates can depend solely on `cs-ipc` for the wire
/// envelope type.
pub use cs_types::IpcEnvelope;

mod envelope;
pub use envelope::*;

/// Maximum frame body size accepted by [`FrameReader`] (16 MiB). Guards against a
/// corrupt or malicious length prefix triggering an enormous allocation.
pub const MAX_FRAME_LEN: u32 = 16 * 1024 * 1024;

/// Errors produced by the IPC codec and async transport.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    /// Underlying transport I/O failure.
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),

    /// A value could not be serialized into MessagePack.
    #[error("encode error: {0}")]
    Encode(#[from] rmp_serde::encode::Error),

    /// A frame body could not be deserialized from MessagePack.
    #[error("decode error: {0}")]
    Decode(#[from] rmp_serde::decode::Error),

    /// The supplied byte slice was too short to contain a complete frame.
    #[error("incomplete frame: need {needed} bytes, have {have}")]
    Incomplete {
        /// Bytes required for a complete frame.
        needed: usize,
        /// Bytes actually available.
        have: usize,
    },

    /// The declared frame length exceeded [`MAX_FRAME_LEN`].
    #[error("frame too large: {0} bytes exceeds maximum of {max}", max = MAX_FRAME_LEN)]
    FrameTooLarge(u32),
}

/// Convenience result type for this crate.
pub type Result<T> = std::result::Result<T, Error>;

/// Encode `msg` into a single length-prefixed MessagePack frame.
///
/// The returned buffer is a 4-byte big-endian length prefix followed by the
/// MessagePack body, ready to be written to a stream.
pub fn encode_frame<T: Serialize>(msg: &T) -> Result<Vec<u8>> {
    let body = rmp_serde::to_vec_named(msg)?;
    let len = body.len();
    if len as u64 > MAX_FRAME_LEN as u64 {
        return Err(Error::FrameTooLarge(len as u32));
    }
    let mut out = Vec::with_capacity(4 + len);
    out.extend_from_slice(&(len as u32).to_be_bytes());
    out.extend_from_slice(&body);
    Ok(out)
}

/// Decode a single length-prefixed MessagePack frame from the start of `bytes`.
///
/// `bytes` must contain the 4-byte prefix plus the full body. Trailing bytes
/// after the frame are ignored. Use [`FrameReader`] for streaming.
pub fn decode_frame<T: DeserializeOwned>(bytes: &[u8]) -> Result<T> {
    if bytes.len() < 4 {
        return Err(Error::Incomplete {
            needed: 4,
            have: bytes.len(),
        });
    }
    let len = u32::from_be_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);
    if len > MAX_FRAME_LEN {
        return Err(Error::FrameTooLarge(len));
    }
    let total = 4 + len as usize;
    if bytes.len() < total {
        return Err(Error::Incomplete {
            needed: total,
            have: bytes.len(),
        });
    }
    let body = &bytes[4..total];
    Ok(rmp_serde::from_slice(body)?)
}

/// Reads length-prefixed MessagePack frames from an async byte stream.
pub struct FrameReader<R> {
    inner: R,
}

impl<R: AsyncRead + Unpin> FrameReader<R> {
    /// Wrap an async reader.
    pub fn new(inner: R) -> Self {
        Self { inner }
    }

    /// Consume the reader, returning the wrapped stream.
    pub fn into_inner(self) -> R {
        self.inner
    }

    /// Read exactly one frame and deserialize its body into `T`.
    ///
    /// Returns `Ok(None)` on a clean end-of-stream (no bytes available before the
    /// next frame); returns an error on a partial/corrupt frame.
    pub async fn read_frame<T: DeserializeOwned>(&mut self) -> Result<Option<T>> {
        let mut len_buf = [0u8; 4];
        match self.inner.read_exact(&mut len_buf).await {
            Ok(_) => {}
            Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => return Ok(None),
            Err(e) => return Err(Error::Io(e)),
        }
        let len = u32::from_be_bytes(len_buf);
        if len > MAX_FRAME_LEN {
            return Err(Error::FrameTooLarge(len));
        }
        let mut body = vec![0u8; len as usize];
        self.inner.read_exact(&mut body).await?;
        Ok(Some(rmp_serde::from_slice(&body)?))
    }
}

/// Writes length-prefixed MessagePack frames to an async byte stream.
pub struct FrameWriter<W> {
    inner: W,
}

impl<W: AsyncWrite + Unpin> FrameWriter<W> {
    /// Wrap an async writer.
    pub fn new(inner: W) -> Self {
        Self { inner }
    }

    /// Consume the writer, returning the wrapped stream.
    pub fn into_inner(self) -> W {
        self.inner
    }

    /// Serialize `msg` into a frame and write it, flushing the stream.
    pub async fn write_frame<T: Serialize>(&mut self, msg: &T) -> Result<()> {
        let frame = encode_frame(msg)?;
        self.inner.write_all(&frame).await?;
        self.inner.flush().await?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use cs_types::{IpcEnvelope, IpcKind};

    #[test]
    fn roundtrip_encode_decode() {
        let env = IpcEnvelope::request("id-1", "ping", serde_json::json!({"n": 7}));
        let bytes = encode_frame(&env).expect("encode");
        // First four bytes are the big-endian length.
        let len = u32::from_be_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);
        assert_eq!(len as usize, bytes.len() - 4);
        let back: IpcEnvelope = decode_frame(&bytes).expect("decode");
        assert_eq!(back.id, "id-1");
        assert_eq!(back.method, "ping");
        assert_eq!(back.kind, IpcKind::Request);
        assert_eq!(back.payload["n"], serde_json::json!(7));
    }

    #[test]
    fn decode_rejects_truncated_input() {
        let env = IpcEnvelope::event("e", "tick", serde_json::json!({}));
        let bytes = encode_frame(&env).unwrap();
        let err = decode_frame::<IpcEnvelope>(&bytes[..bytes.len() - 1]).unwrap_err();
        assert!(matches!(err, Error::Incomplete { .. }));
    }

    #[tokio::test]
    async fn async_reader_writer_roundtrip() {
        let (client, server) = tokio::io::duplex(4096);
        let mut writer = FrameWriter::new(client);
        let mut reader = FrameReader::new(server);

        let env = IpcEnvelope::request("x", "config.get", serde_json::json!({}));
        writer.write_frame(&env).await.expect("write");
        drop(writer); // signal EOF after one frame

        let got: IpcEnvelope = reader.read_frame().await.expect("read").expect("frame");
        assert_eq!(got.method, "config.get");
        // Subsequent read sees clean EOF.
        let none: Option<IpcEnvelope> = reader.read_frame().await.expect("eof");
        assert!(none.is_none());
    }
}
