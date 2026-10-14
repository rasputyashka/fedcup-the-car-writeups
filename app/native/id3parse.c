#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_TAG_SIZE (4U * 1024U * 1024U)

static int synchsafe_u32(const unsigned char raw[4], uint32_t *out) {
    if ((raw[0] | raw[1] | raw[2] | raw[3]) & 0x80U) {
        return -1;
    }
    *out = ((uint32_t)raw[0] << 21U) | ((uint32_t)raw[1] << 14U) |
           ((uint32_t)raw[2] << 7U) | (uint32_t)raw[3];
    return 0;
}

static uint32_t be_u32(const unsigned char raw[4]) {
    return ((uint32_t)raw[0] << 24U) | ((uint32_t)raw[1] << 16U) |
           ((uint32_t)raw[2] << 8U) | (uint32_t)raw[3];
}

static const char *frame_label(const unsigned char id[4]) {
    if (memcmp(id, "TIT2", 4) == 0) {
        return "TITLE";
    }
    if (memcmp(id, "TPE1", 4) == 0) {
        return "ARTIST";
    }
    if (memcmp(id, "COMM", 4) == 0) {
        return "COMM";
    }
    return NULL;
}

static size_t payload_text_offset(const unsigned char id[4],
                                  const unsigned char *payload,
                                  size_t length) {
    size_t cursor;
    unsigned char encoding;

    if (length == 0) {
        return length;
    }
    if (memcmp(id, "COMM", 4) != 0) {
        return 1;
    }
    if (length < 5) {
        return length;
    }

    encoding = payload[0];
    cursor = 4;
    if (encoding == 1 || encoding == 2) {
        while (cursor + 1 < length) {
            if (payload[cursor] == '\0' && payload[cursor + 1] == '\0') {
                return cursor + 2;
            }
            cursor += 2;
        }
        return length;
    }
    while (cursor < length) {
        if (payload[cursor] == '\0') {
            return cursor + 1;
        }
        cursor++;
    }
    return length;
}

static void flatten_line(char *value) {
    size_t i;
    for (i = 0; value[i] != '\0'; ++i) {
        if (value[i] == '\r' || value[i] == '\n') {
            value[i] = ' ';
        }
    }
}

static void emit_frame(const char *label, const unsigned char id[4],
                       const unsigned char *payload, size_t length) {
    size_t offset = payload_text_offset(id, payload, length);
    size_t body_length;
    char *body;
    char buf[64];

    if (offset >= length) {
        return;
    }
    body_length = length - offset;
    body = malloc(body_length + 1);
    if (body == NULL) {
        return;
    }
    memcpy(body, payload + offset, body_length);
    body[body_length] = '\0';

    strcpy(buf, body);

    flatten_line(buf);
    printf("%s=%s\n", label, buf);
    free(body);
}

int main(int argc, char **argv) {
    unsigned char header[10];
    unsigned char *tag = NULL;
    uint32_t tag_size;
    size_t cursor = 0;
    FILE *input;
    int version;

    if (argc != 2) {
        fprintf(stderr, "usage: %s <media-file>\n", argv[0]);
        return 2;
    }
    input = fopen(argv[1], "rb");
    if (input == NULL) {
        perror("open media");
        return 2;
    }
    if (fread(header, 1, sizeof(header), input) != sizeof(header) ||
        memcmp(header, "ID3", 3) != 0) {
        fprintf(stderr, "invalid ID3 header\n");
        fclose(input);
        return 3;
    }
    version = header[3];
    if (version != 3 && version != 4) {
        fprintf(stderr, "unsupported ID3 version\n");
        fclose(input);
        return 3;
    }
    if (synchsafe_u32(&header[6], &tag_size) != 0 || tag_size > MAX_TAG_SIZE) {
        fprintf(stderr, "invalid ID3 tag size\n");
        fclose(input);
        return 3;
    }
    tag = calloc((size_t)tag_size + 1, 1);
    if (tag == NULL) {
        fclose(input);
        return 4;
    }
    if (fread(tag, 1, tag_size, input) != tag_size) {
        fprintf(stderr, "truncated ID3 tag\n");
        free(tag);
        fclose(input);
        return 3;
    }
    fclose(input);

    while (cursor + 10 <= tag_size) {
        const unsigned char *frame = tag + cursor;
        const char *label;
        uint32_t frame_size;
        size_t payload_offset;

        if (frame[0] == '\0' && frame[1] == '\0' && frame[2] == '\0' && frame[3] == '\0') {
            break;
        }
        frame_size = version == 4 ? 0 : be_u32(frame + 4);
        if (version == 4 && synchsafe_u32(frame + 4, &frame_size) != 0) {
            break;
        }
        payload_offset = cursor + 10;
        if (frame_size > tag_size - payload_offset) {
            break;
        }
        label = frame_label(frame);
        if (label != NULL) {
            emit_frame(label, frame, tag + payload_offset, frame_size);
        }
        cursor = payload_offset + frame_size;
    }

    free(tag);
    return 0;
}
