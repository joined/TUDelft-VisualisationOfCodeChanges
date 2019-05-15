package nl.tud.mcextractor;

import java.io.IOException;
import java.nio.file.InvalidPathException;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;

public class Runner {
    public static void main(String[] args) throws InvalidPathException, IOException {
        // The program takes as input, from the command line, the path to the git repository and the list of files affected by the changes
        // that should be used as starting point for the analysis
        if (args.length < 2) {
            System.out.println("Invalid number of arguments. Usage: java -jar mcextractor.jar repository modified_class1.java modified_class2.java [...]");
            System.exit(1);
        }

        // Create Path linked to the repository to analyze
        Path repository = Paths.get(args[0]);

        // Create list of Path objects representing the modified classes
        ArrayList<Path> modified_classes = new ArrayList<>();
        for (int i = 1; i < args.length; i += 1) modified_classes.add(repository.resolve(args[i]));

        MethodCallExtractor mcextractor = new MethodCallExtractor(repository, modified_classes);

        // Do the analysis
        System.err.println("Starting the analysis");
        ArrayList<String> formattedMethodCalls = mcextractor.run();
        // Print the results
        System.out.println(String.join("\n", formattedMethodCalls));
    }
}
