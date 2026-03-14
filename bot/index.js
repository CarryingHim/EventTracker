const {
    default: makeWASocket,
    useMultiFileAuthState,
    DisconnectReason
} = require('@whiskeysockets/baileys');
const qrcode = require('qrcode-terminal');
const axios = require('axios');
const path = require('path');
const fs = require('fs');

const API_URL = process.env.API_URL || 'http://botc:8000';
const GROUP_JID = process.env.GROUP_JID; // e.g. 123456789@g.us
const POLL_INTERVAL = 60000; // Check for new events every minute

let lastCheckedEventId = null;

async function connectToWhatsApp() {
    const { state, saveCreds } = await useMultiFileAuthState(path.join(__dirname, 'auth_info'));

    const sock = makeWASocket({
        auth: state,
        printQRInTerminal: true
    });

    sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect, qr } = update;
        if (qr) {
            console.log('--- SCAN THIS QR CODE WITH WHATSAPP ---');
            qrcode.generate(qr, { small: true });
        }
        if (connection === 'close') {
            const shouldReconnect = (lastDisconnect.error)?.output?.statusCode !== DisconnectReason.loggedOut;
            console.log('connection closed due to ', lastDisconnect.error, ', reconnecting ', shouldReconnect);
            if (shouldReconnect) connectToWhatsApp();
        } else if (connection === 'open') {
            console.log('opened connection');
            startEventMonitor(sock);
        }
    });

    sock.ev.on('creds.update', saveCreds);
}

async function startEventMonitor(sock) {
    console.log('Starting event monitor...');
    
    // Initial fetch to get the baseline (so we don't announce all old events on startup)
    try {
        const response = await axios.get(`${API_URL}/api/events/public`); // We'll need to create a public endpoint
        const events = response.data;
        if (events.length > 0) {
            lastCheckedEventId = events[events.length - 1].id;
        }
    } catch (err) {
        console.error('Failed to fetch initial events:', err.message);
    }

    setInterval(async () => {
        try {
            const response = await axios.get(`${API_URL}/api/events/public`);
            const events = response.data;
            
            // Filter for events created AFTER our last baseline
            const newEvents = lastCheckedEventId 
                ? events.filter(e => !lastCheckedEventId.includes(e.id)) // Simplified logic
                : [];

            for (const event of events) {
                // If we haven't seen this event ID before, announce it
                if (!lastCheckedEventId || !lastCheckedEventId.includes(event.id)) {
                    await announceEvent(sock, event);
                }
            }
            
            if (events.length > 0) {
                lastCheckedEventId = events.map(e => e.id);
            }
        } catch (err) {
            console.error('Error monitoring events:', err.message);
        }
    }, POLL_INTERVAL);
}

async function announceEvent(sock, event) {
    if (!GROUP_JID) {
        console.warn('No GROUP_JID set, cannot announce event.');
        return;
    }

    const message = `🌟 *NEW EVENT CREATED* 🌟\n\n` +
        `🏆 *${event.title}*\n` +
        `📅 ${event.date} at ${event.time}\n` +
        `📍 ${event.location}\n` +
        `👤 Host: ${event.host}\n\n` +
        `👥 Slots: ${event.participants.length}/${event.maxPlayers}\n` +
        `${event.beginner.enabled ? `🌱 Beginner Slot available!` : ''}\n\n` +
        `🔗 Join here: ${process.env.PUBLIC_URL || 'Check EventHub'}`;

    try {
        await sock.sendMessage(GROUP_JID, { text: message });
        console.log(`Announced event: ${event.title}`);
    } catch (err) {
        console.error('Failed to send message:', err);
    }
}

connectToWhatsApp();
