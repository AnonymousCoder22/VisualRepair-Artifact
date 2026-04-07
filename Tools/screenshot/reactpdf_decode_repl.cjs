#!/usr/bin/env node
/* eslint-disable no-console */

function fromCharCode(value) {
  return String.fromCharCode(value);
}

// Minimal vendored subset of lz-string for react-pdf REPL payload decoding.
const LZString = {
  decompress(compressed) {
    if (compressed == null) return '';
    if (compressed === '') return null;
    return this._decompress(compressed.length, 32768, (index) =>
      compressed.charCodeAt(index),
    );
  },

  decompressFromUint8Array(compressed) {
    if (compressed == null) {
      return this.decompress(compressed);
    }

    const buffer = new Array(compressed.length / 2);
    for (let i = 0; i < buffer.length; i += 1) {
      buffer[i] = compressed[i * 2] * 256 + compressed[i * 2 + 1];
    }

    const chars = buffer.map((value) => fromCharCode(value));
    return this.decompress(chars.join(''));
  },

  _decompress(length, resetValue, getNextValue) {
    const dictionary = [];
    let next;
    let enlargeIn = 4;
    let dictSize = 4;
    let numBits = 3;
    let entry = '';
    const result = [];
    let i;
    let bits;
    let resb;
    let maxpower;
    let power;
    let c;
    const data = { val: getNextValue(0), position: resetValue, index: 1 };

    for (i = 0; i < 3; i += 1) dictionary[i] = i;

    bits = 0;
    maxpower = 2 ** 2;
    power = 1;
    while (power !== maxpower) {
      resb = data.val & data.position;
      data.position >>= 1;
      if (data.position === 0) {
        data.position = resetValue;
        data.val = getNextValue(data.index++);
      }
      bits |= (resb > 0 ? 1 : 0) * power;
      power <<= 1;
    }

    switch (bits) {
      case 0:
        bits = 0;
        maxpower = 2 ** 8;
        power = 1;
        while (power !== maxpower) {
          resb = data.val & data.position;
          data.position >>= 1;
          if (data.position === 0) {
            data.position = resetValue;
            data.val = getNextValue(data.index++);
          }
          bits |= (resb > 0 ? 1 : 0) * power;
          power <<= 1;
        }
        c = fromCharCode(bits);
        break;
      case 1:
        bits = 0;
        maxpower = 2 ** 16;
        power = 1;
        while (power !== maxpower) {
          resb = data.val & data.position;
          data.position >>= 1;
          if (data.position === 0) {
            data.position = resetValue;
            data.val = getNextValue(data.index++);
          }
          bits |= (resb > 0 ? 1 : 0) * power;
          power <<= 1;
        }
        c = fromCharCode(bits);
        break;
      case 2:
        return '';
      default:
        c = '';
    }

    dictionary[3] = c;
    let w = c;
    result.push(c);

    while (true) {
      if (data.index > length) return '';

      bits = 0;
      maxpower = 2 ** numBits;
      power = 1;
      while (power !== maxpower) {
        resb = data.val & data.position;
        data.position >>= 1;
        if (data.position === 0) {
          data.position = resetValue;
          data.val = getNextValue(data.index++);
        }
        bits |= (resb > 0 ? 1 : 0) * power;
        power <<= 1;
      }

      let cc;
      switch ((next = bits)) {
        case 0:
          bits = 0;
          maxpower = 2 ** 8;
          power = 1;
          while (power !== maxpower) {
            resb = data.val & data.position;
            data.position >>= 1;
            if (data.position === 0) {
              data.position = resetValue;
              data.val = getNextValue(data.index++);
            }
            bits |= (resb > 0 ? 1 : 0) * power;
            power <<= 1;
          }
          dictionary[dictSize++] = fromCharCode(bits);
          next = dictSize - 1;
          enlargeIn -= 1;
          break;
        case 1:
          bits = 0;
          maxpower = 2 ** 16;
          power = 1;
          while (power !== maxpower) {
            resb = data.val & data.position;
            data.position >>= 1;
            if (data.position === 0) {
              data.position = resetValue;
              data.val = getNextValue(data.index++);
            }
            bits |= (resb > 0 ? 1 : 0) * power;
            power <<= 1;
          }
          dictionary[dictSize++] = fromCharCode(bits);
          next = dictSize - 1;
          enlargeIn -= 1;
          break;
        case 2:
          return result.join('');
        default:
      }

      if (enlargeIn === 0) {
        enlargeIn = 2 ** numBits;
        numBits += 1;
      }

      if (dictionary[next]) {
        entry = dictionary[next];
      } else if (next === dictSize) {
        entry = w + w.charAt(0);
      } else {
        return null;
      }

      result.push(entry);
      dictionary[dictSize++] = w + entry.charAt(0);
      enlargeIn -= 1;
      w = entry;

      if (enlargeIn === 0) {
        enlargeIn = 2 ** numBits;
        numBits += 1;
      }
    }
  },
};

function die(message) {
  console.error(message);
  process.exit(1);
}

const encoded = process.argv[2] || '';

if (!/^[0-9a-f]+$/i.test(encoded) || encoded.length % 2 !== 0) {
  die('invalid repl payload');
}

const bytes = Uint8Array.from(Buffer.from(encoded, 'hex'));
const decoded = LZString.decompressFromUint8Array(bytes);

if (!decoded) {
  die('unable to decode repl payload');
}

process.stdout.write(decoded);
