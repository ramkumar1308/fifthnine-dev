package fixture;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TreeSet;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.actuate.autoconfigure.endpoint.web.WebEndpointProperties;
import org.springframework.boot.actuate.autoconfigure.web.server.ManagementPortType;
import org.springframework.boot.actuate.autoconfigure.web.server.ManagementServerProperties;
import org.springframework.boot.actuate.health.HealthEndpointGroups;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.autoconfigure.context.LifecycleProperties;
import org.springframework.boot.autoconfigure.web.ServerProperties;
import org.springframework.boot.context.properties.bind.Bindable;
import org.springframework.boot.context.properties.bind.Binder;
import org.springframework.boot.context.properties.source.ConfigurationProperty;
import org.springframework.boot.context.properties.source.ConfigurationPropertyName;
import org.springframework.boot.context.properties.source.ConfigurationPropertySource;
import org.springframework.boot.context.properties.source.ConfigurationPropertySources;
import org.springframework.boot.web.context.WebServerApplicationContext;
import org.springframework.context.ApplicationContext;
import org.springframework.context.annotation.Bean;
import org.springframework.core.env.ConfigurableEnvironment;

/**
 * Test fixture for the M2-D3 runtime comparison. After start-up it writes what Spring Boot actually resolved (profiles,
 * property values with their origin, bound defaults, the management port type and the health groups) to the file named by
 * FN_RUNTIME_OUT, for the keys listed in the file named by FN_RUNTIME_KEYS. It reads nothing from the repository under test.
 */
@SpringBootApplication
public class Application {

    public static void main(String[] args) {
        SpringApplication.run(Application.class, args);
    }

    @Bean
    ApplicationRunner fnDump(ConfigurableEnvironment env, ApplicationContext ctx, ServerProperties server,
            ObjectProvider<LifecycleProperties> lifecycle, ObjectProvider<ManagementServerProperties> mgmt,
            ObjectProvider<WebEndpointProperties> web, ObjectProvider<HealthEndpointGroups> groups) {
        return (ApplicationArguments args) -> {
            String outName = System.getenv("FN_RUNTIME_OUT");
            if (outName == null) {
                return;
            }
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("activeProfiles", Arrays.asList(env.getActiveProfiles()));
            m.put("defaultProfiles", Arrays.asList(env.getDefaultProfiles()));
            if (ctx instanceof WebServerApplicationContext w && w.getWebServer() != null) {
                m.put("serverPort", w.getWebServer().getPort());
            }
            m.put("managementPort", env.getProperty("local.management.port"));
            m.put("portType", ManagementPortType.get(env).name());

            Map<String, Object> bound = new LinkedHashMap<>();
            bound.put("server.shutdown", server.getShutdown().name().toLowerCase(Locale.ROOT));
            bound.put("server.servlet.context-path", server.getServlet().getContextPath());
            bound.put("server.error.include-stacktrace", server.getError().getIncludeStacktrace().name().toLowerCase(Locale.ROOT));
            lifecycle.ifAvailable(l -> bound.put("spring.lifecycle.timeout-per-shutdown-phase.ms", l.getTimeoutPerShutdownPhase().toMillis()));
            mgmt.ifAvailable(p -> {
                bound.put("management.server.port", p.getPort());
                bound.put("management.server.base-path", p.getBasePath());
            });
            web.ifAvailable(p -> {
                bound.put("management.endpoints.web.base-path", p.getBasePath());
                bound.put("management.endpoints.web.path-mapping", p.getPathMapping());
                bound.put("management.endpoints.web.exposure.include", new TreeSet<>(p.getExposure().getInclude()));
                bound.put("management.endpoints.web.exposure.exclude", new TreeSet<>(p.getExposure().getExclude()));
            });
            m.put("bound", bound);
            groups.ifAvailable(g -> m.put("healthGroups", new ArrayList<>(new TreeSet<>(g.getNames()))));

            Map<String, Object> values = new LinkedHashMap<>();
            String keysName = System.getenv("FN_RUNTIME_KEYS");
            List<String> keys = keysName == null ? List.of() : Files.readAllLines(Path.of(keysName));
            for (String key : keys) {
                if (key.isBlank()) {
                    continue;
                }
                Map<String, Object> e = new LinkedHashMap<>();
                try {
                    String text = env.getProperty(key);
                    e.put("value", text);
                    if (text == null) {  // a YAML sequence is not one property: bind it as a list
                        Binder.get(env).bind(key, Bindable.listOf(String.class)).ifBound(l -> e.put("list", l));
                    }
                } catch (RuntimeException ex) {
                    e.put("error", ex.toString());
                }
                if (!key.contains("[")) {
                    try {
                        ConfigurationPropertyName name = ConfigurationPropertyName.of(key);
                        for (ConfigurationPropertySource s : ConfigurationPropertySources.get(env)) {
                            ConfigurationProperty p = s.getConfigurationProperty(name);
                            if (p != null) {
                                e.put("origin", String.valueOf(p.getOrigin()));
                                break;
                            }
                        }
                    } catch (RuntimeException ex) {
                        e.put("origin_error", ex.toString());
                    }
                }
                values.put(key, e);
            }
            m.put("values", values);

            Path tmp = Path.of(outName + ".tmp");
            Files.writeString(tmp, new ObjectMapper().writeValueAsString(m));
            Files.move(tmp, Path.of(outName), StandardCopyOption.ATOMIC_MOVE);
        };
    }
}
