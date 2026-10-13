idf_component_register(
    SRCS "main.c"
         "agent_bridge.c"
         "agent_config.c"
         "agent_exec.c"
         "agent_hid.c"
         "agent_keymap.c"
         "agent_link_usb.c"
         "agent_link_wifi.c"
         "agent_text.c"
         "agent_ui.c"
         "../assets/fonts/agent_cjk_14.c"
    INCLUDE_DIRS "."
    REQUIRES bsp bt esp_hid esp_event esp_netif esp_timer esp_wifi nvs_flash
             esp_driver_usb_serial_jtag vfs lwip
)
