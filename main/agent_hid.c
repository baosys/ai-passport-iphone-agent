#include "agent_hid.h"

#include "agent_exec.h"

#include <string.h>

#include "esp_hidd.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "host/ble_hs.h"
#include "host/util/util.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "services/gap/ble_svc_gap.h"

static const char *TAG = "agent_hid";

// NimBLE 的 store 模板实现没有公开头文件(ESP-IDF 示例同样手动声明)。
void ble_store_config_init(void);

#define REPORT_ID_KEYBOARD 1
#define REPORT_ID_MOUSE    2
#define APPEARANCE_HID     0x03C0   // Generic HID:同时是鼠标和键盘

// 两个报告共用一个 HID 服务:键盘(ID 1,含 LED 输出报告)+ 鼠标(ID 2,带滚轮和横向滚动)。
static const uint8_t REPORT_MAP[] = {
    0x05, 0x01, 0x09, 0x06, 0xA1, 0x01,       // Usage Page (Generic Desktop), Usage (Keyboard), Collection (Application)
    0x85, REPORT_ID_KEYBOARD,                 //   Report ID (1)
    0x05, 0x07, 0x19, 0xE0, 0x29, 0xE7,       //   Usage Page (Key Codes), Usage Min/Max (modifiers)
    0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x08, 0x81, 0x02,  // 8 x 1 bit, Input (Data,Var,Abs)
    0x95, 0x01, 0x75, 0x08, 0x81, 0x01,       //   reserved byte, Input (Cnst)
    0x95, 0x05, 0x75, 0x01, 0x05, 0x08, 0x19, 0x01, 0x29, 0x05, 0x91, 0x02,  // 5 LEDs, Output
    0x95, 0x01, 0x75, 0x03, 0x91, 0x01,       //   LED padding, Output (Cnst)
    0x95, 0x06, 0x75, 0x08, 0x15, 0x00, 0x25, 0x65,
    0x05, 0x07, 0x19, 0x00, 0x29, 0x65, 0x81, 0x00,  // 6 key codes, Input (Data,Array)
    0xC0,                                     // End Collection

    0x05, 0x01, 0x09, 0x02, 0xA1, 0x01,       // Usage Page (Generic Desktop), Usage (Mouse), Collection (Application)
    0x85, REPORT_ID_MOUSE,                    //   Report ID (2)
    0x09, 0x01, 0xA1, 0x00,                   //   Usage (Pointer), Collection (Physical)
    0x05, 0x09, 0x19, 0x01, 0x29, 0x03,       //     Usage Page (Buttons), 1..3
    0x15, 0x00, 0x25, 0x01, 0x95, 0x03, 0x75, 0x01, 0x81, 0x02,
    0x95, 0x01, 0x75, 0x05, 0x81, 0x03,       //     padding
    0x05, 0x01, 0x09, 0x30, 0x09, 0x31, 0x09, 0x38,  // X, Y, Wheel
    0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x03, 0x81, 0x06,  // -127..127, Input (Data,Var,Rel)
    0x05, 0x0C, 0x0A, 0x38, 0x02,             //     Usage Page (Consumer), Usage (AC Pan)
    0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x01, 0x81, 0x06,
    0xC0, 0xC0,                               //   End Collection x2
};

static esp_hid_raw_report_map_t s_maps[] = {
    { .data = REPORT_MAP, .len = sizeof(REPORT_MAP) },
};

static esp_hid_device_config_t s_config = {
    .vendor_id = 0x303A,        // Espressif
    .product_id = 0x8000,
    .version = 0x0100,
    .device_name = "AI Passport",
    .manufacturer_name = "FoloToy",
    .serial_number = "0001",
    .report_maps = s_maps,
    .report_maps_len = 1,
};

static esp_hidd_dev_t *s_dev;
static volatile agent_hid_state_t s_state = AGENT_HID_OFF;
static volatile uint16_t s_conn = BLE_HS_CONN_HANDLE_NONE;
static uint8_t s_own_addr_type;
static const char *s_name = "AI Passport";

static int gap_event(struct ble_gap_event *event, void *arg);

static void advertise(void)
{
    struct ble_hs_adv_fields fields = {
        .flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP,
        .appearance = APPEARANCE_HID,
        .appearance_is_present = 1,
        .name = (uint8_t *)s_name,
        .name_len = (uint8_t)strlen(s_name),
        .name_is_complete = 1,
        .uuids16 = (ble_uuid16_t[]) { BLE_UUID16_INIT(0x1812) },  // HID 服务
        .num_uuids16 = 1,
        .uuids16_is_complete = 1,
    };
    int rc = ble_gap_adv_set_fields(&fields);
    if (rc != 0) {
        ESP_LOGE(TAG, "adv fields failed: %d", rc);
        return;
    }
    // iPhone 主动扫描时会取扫描响应;空响应会让 iOS 丢掉名字,设备在蓝牙列表里一闪而过。
    struct ble_hs_adv_fields rsp = {
        .name = (uint8_t *)s_name,
        .name_len = (uint8_t)strlen(s_name),
        .name_is_complete = 1,
        .tx_pwr_lvl = BLE_HS_ADV_TX_PWR_LVL_AUTO,
        .tx_pwr_lvl_is_present = 1,
    };
    rc = ble_gap_adv_rsp_set_fields(&rsp);
    if (rc != 0) {
        ESP_LOGE(TAG, "scan response fields failed: %d", rc);
        return;
    }
    struct ble_gap_adv_params params = {
        .conn_mode = BLE_GAP_CONN_MODE_UND,
        .disc_mode = BLE_GAP_DISC_MODE_GEN,
        .itvl_min = BLE_GAP_ADV_ITVL_MS(30),
        .itvl_max = BLE_GAP_ADV_ITVL_MS(50),
    };
    // 一直广播:这是一个常驻桥接器,iPhone 随时可能回连。
    rc = ble_gap_adv_start(s_own_addr_type, NULL, BLE_HS_FOREVER, &params, gap_event, NULL);
    if (rc != 0 && rc != BLE_HS_EALREADY) {
        ESP_LOGE(TAG, "adv start failed: %d", rc);
        return;
    }
    s_state = AGENT_HID_ADVERTISING;
}

// 请求短连接间隔:报告随连接事件送出,间隔越短指针越跟手。iOS 对 HID 设备接受
// 11.25 ms 起的间隔;最终取值由 iPhone 决定。
static void request_fast_link(uint16_t conn)
{
    const struct ble_gap_upd_params p = {
        .itvl_min = 9,              // 11.25 ms(单位 1.25 ms)
        .itvl_max = 12,             // 15 ms
        .latency = 0,
        .supervision_timeout = 200, // 2 s(单位 10 ms)
    };
    int rc = ble_gap_update_params(conn, &p);
    if (rc != 0) ESP_LOGW(TAG, "conn param update request failed: %d", rc);
}

static int gap_event(struct ble_gap_event *event, void *arg)
{
    (void)arg;
    struct ble_gap_conn_desc desc;
    switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
        if (event->connect.status != 0) {
            advertise();
            return 0;
        }
        s_conn = event->connect.conn_handle;
        s_state = AGENT_HID_CONNECTED;
        ESP_LOGI(TAG, "phone connected");
        // 已绑定的手机回连时直接用保存的密钥加密;新手机由这里触发配对。
        (void)ble_gap_security_initiate(s_conn);
        return 0;
    case BLE_GAP_EVENT_DISCONNECT:
        ESP_LOGI(TAG, "phone disconnected: reason=%d", event->disconnect.reason);
        s_conn = BLE_HS_CONN_HANDLE_NONE;
        advertise();
        return 0;
    case BLE_GAP_EVENT_ENC_CHANGE:
        if (event->enc_change.status == 0) {
            s_state = AGENT_HID_READY;
            ESP_LOGI(TAG, "link encrypted, HID ready");
            request_fast_link(event->enc_change.conn_handle);
        } else {
            ESP_LOGW(TAG, "encryption failed: %d", event->enc_change.status);
        }
        return 0;
    case BLE_GAP_EVENT_CONN_UPDATE:
        if (ble_gap_conn_find(event->conn_update.conn_handle, &desc) == 0) {
            ESP_LOGI(TAG, "conn interval %d x 1.25 ms, latency %d",
                     desc.conn_itvl, desc.conn_latency);
        }
        return 0;
    case BLE_GAP_EVENT_REPEAT_PAIRING:
        // iPhone 端「忽略此设备」后重新配对:丢掉旧绑定,接受新的。
        if (ble_gap_conn_find(event->repeat_pairing.conn_handle, &desc) == 0) {
            ble_store_util_delete_peer(&desc.peer_id_addr);
        }
        return BLE_GAP_REPEAT_PAIRING_RETRY;
    case BLE_GAP_EVENT_PASSKEY_ACTION:
        // 无输入输出能力 → Just Works;理论上不会走到这里,收到数值比较请求时直接接受。
        if (event->passkey.params.action == BLE_SM_IOACT_NUMCMP) {
            struct ble_sm_io io = { .action = BLE_SM_IOACT_NUMCMP, .numcmp_accept = 1 };
            ble_sm_inject_io(event->passkey.conn_handle, &io);
        }
        return 0;
    case BLE_GAP_EVENT_ADV_COMPLETE:
        if (s_conn == BLE_HS_CONN_HANDLE_NONE) advertise();
        return 0;
    default:
        return 0;
    }
}

static void hidd_event(void *handler_args, esp_event_base_t base, int32_t id, void *event_data)
{
    (void)handler_args;
    (void)base;
    (void)event_data;
    if (id == ESP_HIDD_START_EVENT) {
        // 主机栈已同步:确定地址类型后开始广播。
        if (ble_hs_util_ensure_addr(0) != 0 || ble_hs_id_infer_auto(0, &s_own_addr_type) != 0) {
            ESP_LOGE(TAG, "no usable BLE address");
            return;
        }
        advertise();
    }
}

static void host_task(void *param)
{
    (void)param;
    nimble_port_run();
    nimble_port_freertos_deinit();
}

esp_err_t agent_hid_start(const char *name)
{
    if (s_dev) return ESP_OK;
    if (name && *name) {
        s_name = name;
        s_config.device_name = name;
    }
    esp_err_t err = nimble_port_init();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nimble_port_init: %s", esp_err_to_name(err));
        return err;
    }

    // Just Works 绑定 + LE Secure Connections:iPhone 弹一次「配对」确认即可,无需输入 PIN。
    ble_hs_cfg.sm_io_cap = BLE_SM_IO_CAP_NO_IO;
    ble_hs_cfg.sm_bonding = 1;
    ble_hs_cfg.sm_mitm = 0;
    ble_hs_cfg.sm_sc = 1;
    ble_hs_cfg.sm_our_key_dist = BLE_SM_PAIR_KEY_DIST_ENC | BLE_SM_PAIR_KEY_DIST_ID;
    ble_hs_cfg.sm_their_key_dist = BLE_SM_PAIR_KEY_DIST_ENC | BLE_SM_PAIR_KEY_DIST_ID;
    ble_hs_cfg.store_status_cb = ble_store_util_status_rr;

    // esp_hidd 注册 GATT 服务(HID / 电池 / 设备信息)并接管 sync_cb,
    // 主机栈就绪后通过 ESP_HIDD_START_EVENT 通知我们开始广播。
    err = esp_hidd_dev_init(&s_config, ESP_HID_TRANSPORT_BLE, hidd_event, &s_dev);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_hidd_dev_init: %s", esp_err_to_name(err));
        return err;
    }
    ble_svc_gap_device_name_set(s_name);
    ble_svc_gap_device_appearance_set(APPEARANCE_HID);
    ble_store_config_init();
    nimble_port_freertos_init(host_task);
    return ESP_OK;
}

agent_hid_state_t agent_hid_state(void)
{
    return s_state;
}

// NimBLE 的 mbuf 池很小;报告节拍快于连接间隔时会堆积。等它腾出空间再发,
// 而不是让 esp_hidd 内部分配失败。
static bool wait_for_buffers(void)
{
    for (int i = 0; i < 40; i++) {
        if (os_msys_num_free() >= 4) return true;
        vTaskDelay(pdMS_TO_TICKS(5));
        if (s_state != AGENT_HID_READY) return false;
    }
    return false;
}

static bool send(uint8_t id, const uint8_t *report, size_t len)
{
    for (int attempt = 0; attempt < 3; attempt++) {
        if (s_state != AGENT_HID_READY || !wait_for_buffers()) return false;
        if (esp_hidd_dev_input_set(s_dev, 0, id, (uint8_t *)report, len) == ESP_OK) return true;
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    return false;
}

bool agent_hid_mouse(const uint8_t *report)
{
    return send(REPORT_ID_MOUSE, report, AGENT_MOUSE_REPORT_LEN);
}

bool agent_hid_keyboard(const uint8_t *report)
{
    return send(REPORT_ID_KEYBOARD, report, AGENT_KEY_REPORT_LEN);
}

esp_err_t agent_hid_forget(void)
{
    if (!s_dev) return ESP_ERR_INVALID_STATE;
    uint16_t conn = s_conn;
    if (conn != BLE_HS_CONN_HANDLE_NONE) {
        ble_gap_terminate(conn, BLE_ERR_REM_USER_CONN_TERM);
    }
    int rc = ble_store_clear();
    ESP_LOGI(TAG, "bonds cleared: %d", rc);
    return rc == 0 ? ESP_OK : ESP_FAIL;
}

void agent_hid_set_battery(uint8_t percent)
{
    if (s_dev && s_state == AGENT_HID_READY) {
        (void)esp_hidd_dev_battery_set(s_dev, percent);
    }
}
