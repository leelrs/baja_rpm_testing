// =============================================================================
// RPM Capture Firmware — Teensy 4.1
// =============================================================================
// Hardware:
//   - Pin 0: Optocoupler output from gear tooth sensor (Channel A) for primary, 
//   - Pin 1: Optocoupler output from gear tooth sensor (Channel B ) for secondary.
//
// Signal: ~628 Hz pulse train from optocoupler for secondary.
// Signal
// Output: .bin file to sd card
//
// .bin file format (little-endian, packed structs):
//   [Header: 16 bytes]
//     uint32_t magic        = 0x52504D31  ("RPM1")
//     uint32_t teeth        = GEAR_TEETH
//     uint32_t sample_rate  = REPORT_INTERVAL_US (microseconds)
//     uint32_t reserved     = 0
//   [Records: 12 bytes each, repeated]
//     uint32_t timestamp_us  — micros() at sample time
//     float    rpm           — computed RPM
//     uint32_t pulse_count   — raw pulses in this window
// =============================================================================

#include <Arduino.h>
#include <SD.h>
#include <SPI.h>

// config

#define SENSOR_PIN_A        0        // Primary optocoupler GPIO pin
#define SENSOR_PIN_B        1         // Secondary optocoupler pin

#define GEAR_TEETH_PRIM         6         // 6 pins on the bottom of the prim cvt. 6 pulses = 1 revolution
#define GEAR_TEETH_SEC          16        // 16-tooth gear: 16 pulses = 1 revolution

#define REPORT_INTERVAL_US  2000     // Reporting window in microseconds (2 ms = 500 Hz output)
 // Smaller = faster response, noisier
 // Larger  = smoother, more latency

#define ROLLING_AVG_SAMPLES 8         // Number of RPM samples to average together
// 1 = instantaneous (no smoothing)
 // 8 = smooth rolling average

#define OPTOCOUPLER_ACTIVE  HIGH      // Signal level when gear tooth is detected. light turns on when conducting -> high
 
  
#define SD_CS_PIN BUILTIN_SDCARD
File logFileA;
File logFileB;

#define SERIAL_BAUD         2000000   // USB serial baud rate (2 Mbps for Teensy USB serial)

// binary stuff

#define MAGIC_NUMBER        0x52504D31UL  // spells out "RPM1" in ascii lol. its just so the python plotter only plots files from this firmware that have those starting numbers.

// Header struct sent once at startup
struct __attribute__((packed)) FileHeader {
    uint32_t magic;
    uint32_t teeth;
    uint32_t sample_interval_us;
    uint32_t reserved;
};

// Data record struct sent every REPORT_INTERVAL_US
struct __attribute__((packed)) RPMRecord {
    uint32_t timestamp_us;
    float    rpm;
    uint32_t pulse_count;
};

// global vars

// Pulse-interval rolling average buffer
// Each slot stores the microsecond gap between consecutive pulses 
// rpm is derived from the mean of the last N intervals:
//   rpm = 60,000,000 / (mean_interval_us * GEAR_TEETH)

// Separate interval ring buffers for each channel
volatile uint32_t intervalBufA[ROLLING_AVG_SAMPLES];
volatile uint32_t intervalBufB[ROLLING_AVG_SAMPLES];
volatile uint8_t  intervalHeadA = 0, intervalHeadB = 0;
volatile uint8_t  intervalCountA = 0, intervalCountB = 0;
volatile uint32_t lastPulseTimeA = 0, lastPulseTimeB = 0;

uint32_t lastReportTime = 0;
uint32_t lastFlushTime = 0;

// ─── ISR: RECORD PULSE INTERVALS ──────────────────────────────────────────────
// Instead of just counting pulses, we store the time gap between successive
// pulses. no need for a fixed window now.



void FASTRUN isr_pin_a() {
    uint32_t now = micros();
    uint32_t gap = now - lastPulseTimeA;
    lastPulseTimeA = now;
    if (gap < 100) return;
    intervalBufA[intervalHeadA] = gap;
    intervalHeadA = (intervalHeadA + 1) % ROLLING_AVG_SAMPLES;
    if (intervalCountA < ROLLING_AVG_SAMPLES) intervalCountA++;
}

void FASTRUN isr_pin_b() {
    uint32_t now = micros();
    uint32_t gap = now - lastPulseTimeB;
    lastPulseTimeB = now;
    if (gap < 100) return;
    intervalBufB[intervalHeadB] = gap;
    intervalHeadB = (intervalHeadB + 1) % ROLLING_AVG_SAMPLES;
    if (intervalCountB < ROLLING_AVG_SAMPLES) intervalCountB++;
}

// rolling average rpm
// Must be called with interrupts disabled (or from a snapshot of the buffer).

float computeRollingRPM(const uint32_t* buf, uint8_t head, uint8_t count, uint8_t teeth) {
    if (count == 0) return 0.0f;
    uint64_t sum = 0;
    for (uint8_t i = 0; i < count; i++) {
        uint8_t idx = (head + ROLLING_AVG_SAMPLES - 1 - i) % ROLLING_AVG_SAMPLES;
        sum += buf[idx];
    }
    float meanInterval_us = (float)sum / count;
    return 60000000.0f / (meanInterval_us * teeth);
}

// setup!

void setup() {

//the optocoupler light turns on whenever the gear tooth sees a hit. so its active high
//forced pulldown, if there is no data thru one of the lines the pin's just floating on the teensy so pull it low.
//trigger edge to rising as its active high.
pinMode(SENSOR_PIN_A, INPUT_PULLDOWN);
pinMode(SENSOR_PIN_B, INPUT_PULLDOWN);
attachInterrupt(digitalPinToInterrupt(SENSOR_PIN_A), isr_pin_a, RISING);
attachInterrupt(digitalPinToInterrupt(SENSOR_PIN_B), isr_pin_b, RISING);


//for debugging mostly.
Serial.begin(115200);
while (!Serial && millis() < 3000);  // wait up to 3s for Serial Monitor to connec

SD.begin(BUILTIN_SDCARD);

//did u forget the sd card??
if (!SD.begin(BUILTIN_SDCARD)) {
    Serial.println("[ERROR] SD card not found! Halting. CTRL+C to exit.");
    while (1);  // stop here no point running without SD
}
Serial.println("[OK] SD card initialized.");

// --- Channel A file ---
char filenameA[28];
uint32_t idx = 0;
do {
    snprintf(filenameA, sizeof(filenameA), "RPM_A_%04lu.bin", idx++);
} while (SD.exists(filenameA));
logFileA = SD.open(filenameA, FILE_WRITE);

FileHeader hdrA;
hdrA.magic              = MAGIC_NUMBER;
hdrA.teeth              = GEAR_TEETH_PRIM;
hdrA.sample_interval_us = REPORT_INTERVAL_US;
hdrA.reserved           = 0;
logFileA.write((const uint8_t*)&hdrA, sizeof(hdrA));
logFileA.flush();

//prim file status
Serial.print("[OK] Logging Channel A → ");
Serial.println(filenameA);

// --- Channel B file ---
// Reuse same idx counter so both files share the same run number
char filenameB[28];
snprintf(filenameB, sizeof(filenameB), "RPM_B_%04lu.bin", idx - 1);
logFileB = SD.open(filenameB, FILE_WRITE);

FileHeader hdrB;
hdrB.magic              = MAGIC_NUMBER;
hdrB.teeth              = GEAR_TEETH_SEC;
hdrB.sample_interval_us = REPORT_INTERVAL_US;
hdrB.reserved           = 0;
logFileB.write((const uint8_t*)&hdrB, sizeof(hdrB));
logFileB.flush();

// sec file status
Serial.print("[OK] Logging Channel B → ");
Serial.println(filenameB);
}

// main loop

void loop() {
    //for showing rpm in terminal. 
    static uint32_t lastPrintTime = 0;

    uint32_t now = micros();
    if ((now - lastReportTime) < REPORT_INTERVAL_US) return;
    lastReportTime = now;

  //for showing rpm in terminal. 
    static uint32_t lastPrintTime = 0;


    // channel A snapshot (prim)
    noInterrupts();
    uint32_t snapBufA[ROLLING_AVG_SAMPLES];
    memcpy(snapBufA, (const void*)intervalBufA, sizeof(snapBufA));
    uint8_t  snapHeadA  = intervalHeadA;
    uint8_t  snapCountA = intervalCountA;
    bool     pinAAlive  = (now - lastPulseTimeA) < 500000UL;
    interrupts();

    RPMRecord recA;
    recA.timestamp_us = now;
    recA.rpm = (snapCountA > 0 && pinAAlive)
           ? computeRollingRPM(snapBufA, snapHeadA, snapCountA, GEAR_TEETH_PRIM)
           : 0.0f;
    recA.pulse_count  = snapCountA;
    logFileA.write((const uint8_t*)&recA, sizeof(recA));
    

    //channel b snapshot (sec)
    noInterrupts();
    uint32_t snapBufB[ROLLING_AVG_SAMPLES];
    memcpy(snapBufB, (const void*)intervalBufB, sizeof(snapBufB));
    uint8_t  snapHeadB  = intervalHeadB;
    uint8_t  snapCountB = intervalCountB;
    bool     pinBAlive  = (now - lastPulseTimeB) < 500000UL;
    interrupts();

    RPMRecord recB;
    recB.timestamp_us = now;
    recB.rpm = (snapCountB > 0 && pinBAlive)
           ? computeRollingRPM(snapBufB, snapHeadB, snapCountB, GEAR_TEETH_SEC)
           : 0.0f;
    recB.pulse_count  = snapCountB;
    logFileB.write((const uint8_t*)&recB, sizeof(recB));
   

    //shows rpm as u go in terminal 
        if ((now - lastPrintTime) >= 1000000UL) {  // every 1 second
    lastPrintTime = now;
    Serial.print("[LOG] t=");
    Serial.print(now / 1000000UL);
    Serial.print("s  |  A: ");
    Serial.print(recA.rpm, 1);
    Serial.print(" RPM  |  B: ");
    Serial.print(recB.rpm, 1);
    Serial.print(" RPM  |  A pulses: ");
    Serial.print(recA.pulse_count);
    Serial.print("  B pulses: ");
    Serial.println(recB.pulse_count);

         if (millis() - lastFlushTime > 1000) {   // flush every 1 second
    logFileA.flush();
    logFileB.flush();
    lastFlushTime = millis();
      }
   }
}
