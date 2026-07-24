include $(TOPDIR)/rules.mk

PKG_NAME:=ps2servers-edge
PKG_VERSION:=0.5.0
PKG_RELEASE:=1
PKG_LICENSE:=AFL-3.0
PKG_LICENSE_FILES:=LICENSE
PKG_MAINTAINER:=PS2 Servers contributors
PKG_BUILD_DEPENDS:=golang/host
PKG_BUILD_PARALLEL:=1

GO_PKG:=github.com/NathanNeurotic/PS2-Servers/native/ps2servers-edge
GO_PKG_BUILD_PKG:=$(GO_PKG)/cmd/ps2servers-edge

include $(INCLUDE_DIR)/package.mk
include ../../lang/golang/golang-package.mk

define Package/ps2servers-edge
  SECTION:=net
  CATEGORY:=Network
  TITLE:=PS2 Servers Edge UDPFS server
  URL:=https://github.com/NathanNeurotic/PS2-Servers
  USERID:=ps2edge=453:ps2edge=453
endef

define Package/ps2servers-edge/description
 A small read-only UDPFS server for PlayStation 2 clients, including automatic
 per-session compatibility for standards-compatible and Modulo clients.
endef

# Point PS2SERVERS_REPO_DIR at a checked-out repository when building this
# package from an OpenWrt SDK. Release automation may replace this with a pinned
# source archive and hash without changing the package/runtime layout.
PS2SERVERS_REPO_DIR?=$(TOPDIR)/../PS2-Servers

define Build/Prepare
	$(RM) -r $(PKG_BUILD_DIR)
	$(INSTALL_DIR) $(PKG_BUILD_DIR)
	$(CP) $(PS2SERVERS_REPO_DIR)/native/ps2servers-edge/. $(PKG_BUILD_DIR)/
	$(CP) $(PS2SERVERS_REPO_DIR)/LICENSE $(PKG_BUILD_DIR)/LICENSE
endef

define Package/ps2servers-edge/conffiles
/etc/config/ps2servers-edge
endef

define Package/ps2servers-edge/install
	$(call GoPackage/Package/Install/Bin,$(1))
	$(INSTALL_DIR) $(1)/etc/config
	$(INSTALL_CONF) ./files/ps2servers-edge.config $(1)/etc/config/ps2servers-edge
	$(INSTALL_DIR) $(1)/etc/init.d
	$(INSTALL_BIN) ./files/ps2servers-edge.init $(1)/etc/init.d/ps2servers-edge
endef

$(eval $(call GoBinPackage,ps2servers-edge))
$(eval $(call BuildPackage,ps2servers-edge))
