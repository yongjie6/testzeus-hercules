Feature: 浏览器链路冒烟

  # 不是产品用例，只用来确认浏览器那条链路本身还通。
  # 产品的 Web 用例进 opt/cases/web/<模块>/。

  Scenario: WEB-SMOKE-01 打开一个公开网页并读到标题
    Given I have a web browser open
    When I navigate to "https://www.google.com"
    Then the page title should contain "Google"
