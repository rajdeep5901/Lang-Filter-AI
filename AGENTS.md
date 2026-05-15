# Project: Real-Time Language-Based Selective Audio Denoising

## Core Architecture

* Backend: Python
* Frontend: HTML/CSS/JS
* API: Flask
* AI Model: faster-whisper (Whisper base int8)
* Audio I/O: sounddevice

## Critical Rules

* DO NOT rewrite architecture
* DO NOT replace Whisper
* DO NOT change threading architecture
* DO NOT remove smoothing logic
* Preserve Flask API structure

## Current System

Mic → Whisper → Smoothing → Decision → Volume Fade → Output

Frontend polls `/status` endpoint every 500ms.

## Goals

* Improve language detection accuracy
* Improve frontend/backend synchronization
* Improve Raspberry Pi deployment readiness
* Reduce false positives
* Stabilize smoothing logic
* Maintain low latency

## Constraints

* CPU-only inference
* Raspberry Pi compatible
* No GPU assumptions
* Real-time operation required
